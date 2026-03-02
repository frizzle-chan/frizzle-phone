import asyncio
import logging
import queue
import struct
from unittest.mock import MagicMock, patch

from frizzle_phone.bridge import PhoneAudioSink, PhoneAudioSource
from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.receive import RtpReceiveProtocol

# ---------------------------------------------------------------------------
# BridgeStats unit tests
# ---------------------------------------------------------------------------


def test_bridge_stats_reset():
    stats = BridgeStats()
    stats.d2p_frames_in = 10
    stats.p2d_frames_in = 5
    stats.rtp_frames_sent = 20
    stats._d2p_last_write = 1234.0
    stats._p2d_last_recv = 5678.0
    stats.reset()
    assert stats.d2p_frames_in == 0
    assert stats.p2d_frames_in == 0
    assert stats.rtp_frames_sent == 0
    assert stats._d2p_last_write == 0.0
    assert stats._p2d_last_recv == 0.0


def test_log_summary_emits_info(caplog):
    stats = BridgeStats()
    stats.d2p_frames_in = 250
    stats.p2d_frames_in = 200
    stats.rtp_frames_sent = 250
    with caplog.at_level(logging.INFO, logger="frizzle_phone.bridge_stats"):
        stats.log_summary()
    assert any("bridge stats" in r.message for r in caplog.records)
    assert any("d2p in=250" in r.message for r in caplog.records)


def test_log_summary_resets_counters():
    stats = BridgeStats()
    stats.d2p_frames_in = 100
    stats.p2d_reads = 100
    stats.rtp_frames_sent = 100
    stats.log_summary()
    assert stats.d2p_frames_in == 0
    assert stats.p2d_reads == 0
    assert stats.rtp_frames_sent == 0


def test_log_summary_warns_on_d2p_overflow(caplog):
    stats = BridgeStats()
    stats.d2p_queue_overflow = 3
    stats.rtp_frames_sent = 100
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_summary()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("d2p queue overflow" in r.message for r in warnings)


def test_log_summary_warns_on_high_silence_reads(caplog):
    stats = BridgeStats()
    stats.p2d_reads = 100
    stats.p2d_silence_reads = 30  # 30% > 20%
    stats.rtp_frames_sent = 100
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_summary()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("p2d underflow" in r.message for r in warnings)


def test_log_summary_warns_on_high_silence_sends(caplog):
    stats = BridgeStats()
    stats.rtp_frames_sent = 100
    stats.rtp_silence_sent = 25  # 25% > 20%
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_summary()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("d2p starvation" in r.message for r in warnings)


def test_log_summary_no_warn_when_below_thresholds(caplog):
    stats = BridgeStats()
    stats.rtp_frames_sent = 100
    stats.rtp_silence_sent = 10  # 10% < 20%
    stats.p2d_reads = 100
    stats.p2d_silence_reads = 10  # 10% < 20%
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_summary()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Integration: PhoneAudioSink with stats
# ---------------------------------------------------------------------------


def test_sink_write_increments_d2p_frames_in():
    stats = BridgeStats()
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
    sink = PhoneAudioSink(q, loop, stats=stats)

    user = MagicMock()
    user.id = 1
    data = MagicMock()
    data.pcm = b"\x00" * 3840

    sink.write(user, data)
    assert stats.d2p_frames_in == 1


def test_sink_flush_increments_d2p_frames_mixed():
    stats = BridgeStats()
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
    sink = PhoneAudioSink(q, loop, stats=stats)

    user = MagicMock()
    user.id = 1
    data = MagicMock()
    data.pcm = b"\x00" * 3840

    t = 1000.0
    with patch("frizzle_phone.bridge.time") as mock_time:
        mock_time.monotonic.return_value = t
        sink.write(user, data)
        # Advance past batch threshold to trigger flush
        mock_time.monotonic.return_value = t + 0.020
        sink.write(user, data)

    assert stats.d2p_frames_mixed == 1


def test_sink_stale_batch_increments_d2p_stale_flush():
    stats = BridgeStats()
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
    sink = PhoneAudioSink(q, loop, stats=stats)

    user = MagicMock()
    user.id = 1
    data = MagicMock()
    data.pcm = b"\x00" * 3840

    t = 1000.0
    with patch("frizzle_phone.bridge.time") as mock_time:
        mock_time.monotonic.return_value = t
        sink.write(user, data)
        # Advance past stale threshold (>60ms)
        mock_time.monotonic.return_value = t + 0.070
        sink.write(user, data)

    assert stats.d2p_stale_flush == 1


# ---------------------------------------------------------------------------
# Integration: PhoneAudioSource with stats
# ---------------------------------------------------------------------------


def test_source_read_increments_p2d_reads():
    stats = BridgeStats()
    q: queue.Queue[bytes] = queue.Queue()
    source = PhoneAudioSource(q, stats=stats)
    source.read()
    assert stats.p2d_reads == 1
    assert stats.p2d_silence_reads == 1


def test_source_read_with_data_no_silence():
    stats = BridgeStats()
    q: queue.Queue[bytes] = queue.Queue()
    q.put(b"\x42" * 3840)
    source = PhoneAudioSource(q, stats=stats)
    source.read()
    assert stats.p2d_reads == 1
    assert stats.p2d_silence_reads == 0


# ---------------------------------------------------------------------------
# Integration: RtpReceiveProtocol with stats
# ---------------------------------------------------------------------------


def _build_rtp_packet(payload: bytes) -> bytes:
    """Build a minimal RTP packet for testing."""
    first_byte = 0x80  # V=2
    header = struct.pack("!BBHII", first_byte, 0, 0, 0, 0)
    return header + payload


def test_rtp_receive_increments_p2d_frames_in():
    stats = BridgeStats()
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q, stats=stats)
    payload = b"\xff" * 160
    proto.datagram_received(_build_rtp_packet(payload), ("127.0.0.1", 9000))
    assert stats.p2d_frames_in == 1
