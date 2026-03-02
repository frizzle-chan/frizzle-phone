"""Unit tests for rtp_send_loop: silence, wrapping, drift, stats."""

import asyncio
import struct
from unittest.mock import patch

import pytest

from frizzle_phone.bridge import ULAW_SILENCE_PAYLOAD, rtp_send_loop
from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.stream import SAMPLES_PER_PACKET
from tests.conftest import FakeTransport

_ADDR = ("127.0.0.1", 5000)
_SSRC = 0xDEADBEEF


def _parse_rtp_header(data: bytes) -> tuple[int, int, int, int, int]:
    """Parse RTP header → (marker, payload_type, seq, timestamp, ssrc)."""
    _b0, b1, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])
    marker = (b1 >> 7) & 1
    pt = b1 & 0x7F
    return marker, pt, seq, ts, ssrc


class _StoppingTransport(FakeTransport):
    """FakeTransport that sets stop_event after max_packets sends."""

    def __init__(self, stop_event: asyncio.Event, max_packets: int) -> None:
        super().__init__()
        self._stop = stop_event
        self._max = max_packets

    def sendto(self, data, addr=None):
        super().sendto(data, addr)
        if len(self.sent) >= self._max:
            self._stop.set()


async def _noop_sleep(_dur: float) -> None:
    pass


@pytest.mark.asyncio
@patch("frizzle_phone.bridge.random.randint", side_effect=[_SSRC, 0, 0])
async def test_sends_silence_on_empty_queue(_mock_rand):
    """Empty queue → ULAW_SILENCE_PAYLOAD in RTP packet."""
    stop = asyncio.Event()
    transport = _StoppingTransport(stop, max_packets=1)

    with patch("asyncio.sleep", new=_noop_sleep):
        await rtp_send_loop(asyncio.Queue(), transport, _ADDR, stop_event=stop)

    assert len(transport.sent) == 1
    pkt, dst = transport.sent[0]
    assert dst == _ADDR
    assert pkt[12:] == ULAW_SILENCE_PAYLOAD


@pytest.mark.asyncio
@patch("frizzle_phone.bridge.random.randint", side_effect=[_SSRC, 0, 0])
async def test_sends_queued_payload(_mock_rand):
    """Enqueued payload appears in RTP packet."""
    stop = asyncio.Event()
    transport = _StoppingTransport(stop, max_packets=1)
    q: asyncio.Queue[bytes] = asyncio.Queue()
    payload = b"\xab" * 160
    q.put_nowait(payload)

    with patch("asyncio.sleep", new=_noop_sleep):
        await rtp_send_loop(q, transport, _ADDR, stop_event=stop)

    pkt, _ = transport.sent[0]
    assert pkt[12:] == payload


@pytest.mark.asyncio
@patch("frizzle_phone.bridge.random.randint", side_effect=[_SSRC, 0xFFFF, 0])
async def test_seq_wraps_at_uint16(_mock_rand):
    """Sequence number wraps from 0xFFFF to 0."""
    stop = asyncio.Event()
    transport = _StoppingTransport(stop, max_packets=2)

    with patch("asyncio.sleep", new=_noop_sleep):
        await rtp_send_loop(asyncio.Queue(), transport, _ADDR, stop_event=stop)

    _, _, seq1, _, _ = _parse_rtp_header(transport.sent[0][0])
    _, _, seq2, _, _ = _parse_rtp_header(transport.sent[1][0])
    assert seq1 == 0xFFFF
    assert seq2 == 0


@pytest.mark.asyncio
@patch(
    "frizzle_phone.bridge.random.randint",
    side_effect=[_SSRC, 0, 0xFFFFFFFF - SAMPLES_PER_PACKET + 1],
)
async def test_timestamp_wraps_at_uint32(_mock_rand):
    """Timestamp wraps from near-max to 0."""
    stop = asyncio.Event()
    transport = _StoppingTransport(stop, max_packets=2)

    with patch("asyncio.sleep", new=_noop_sleep):
        await rtp_send_loop(asyncio.Queue(), transport, _ADDR, stop_event=stop)

    _, _, _, ts1, _ = _parse_rtp_header(transport.sent[0][0])
    _, _, _, ts2, _ = _parse_rtp_header(transport.sent[1][0])
    assert ts1 == 0xFFFFFFFF - SAMPLES_PER_PACKET + 1
    assert ts2 == 0


@pytest.mark.asyncio
@patch("frizzle_phone.bridge.random.randint", side_effect=[_SSRC, 0, 0])
async def test_marker_bit_first_packet_only(_mock_rand):
    """First packet has marker bit set; subsequent packets do not."""
    stop = asyncio.Event()
    transport = _StoppingTransport(stop, max_packets=3)

    with patch("asyncio.sleep", new=_noop_sleep):
        await rtp_send_loop(asyncio.Queue(), transport, _ADDR, stop_event=stop)

    m1, _, _, _, _ = _parse_rtp_header(transport.sent[0][0])
    m2, _, _, _, _ = _parse_rtp_header(transport.sent[1][0])
    m3, _, _, _, _ = _parse_rtp_header(transport.sent[2][0])
    assert m1 == 1
    assert m2 == 0
    assert m3 == 0


@pytest.mark.asyncio
@patch("frizzle_phone.bridge.random.randint", side_effect=[_SSRC, 0, 0])
async def test_records_stats(_mock_rand):
    """Stats counters are updated correctly."""
    stop = asyncio.Event()
    transport = _StoppingTransport(stop, max_packets=2)
    q: asyncio.Queue[bytes] = asyncio.Queue()
    q.put_nowait(b"\xab" * 160)  # first frame: real data
    # second frame: queue empty → silence
    stats = BridgeStats()

    with patch("asyncio.sleep", new=_noop_sleep):
        await rtp_send_loop(q, transport, _ADDR, stop_event=stop, stats=stats)

    assert stats.rtp_frames_sent == 2
    assert stats.rtp_silence_sent == 1


@pytest.mark.asyncio
async def test_drift_correction_snaps_forward():
    """When wall clock jumps ahead >1 ptime, next_send snaps to now."""
    stop = asyncio.Event()
    transport = _StoppingTransport(stop, max_packets=2)

    # time.monotonic() call sequence:
    #   1. next_send init → 1000.0
    #   2. now after 1st send → 1000.200 (200ms jump triggers drift snap)
    #   3. now after 2nd send → 1000.220
    _times = [1000.0, 1000.200, 1000.220]
    _idx = [0]

    def mock_monotonic():
        i = _idx[0]
        _idx[0] += 1
        return _times[i] if i < len(_times) else _times[-1]

    with (
        patch("frizzle_phone.bridge.random.randint", side_effect=[_SSRC, 0, 0]),
        patch("frizzle_phone.bridge.time.monotonic", side_effect=mock_monotonic),
        patch("asyncio.sleep", new=_noop_sleep),
    ):
        await rtp_send_loop(asyncio.Queue(), transport, _ADDR, stop_event=stop)

    # Exactly 2 packets — no burst of catch-up sends
    assert len(transport.sent) == 2
