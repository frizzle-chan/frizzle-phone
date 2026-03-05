import queue

from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.receive import RtpReceiveProtocol
from tests.conftest import build_rtp_packet


def test_rtp_receive_extracts_payload():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
    # LQ sinc resampler needs a few input packets to prime the filter
    for _ in range(10):
        proto.datagram_received(build_rtp_packet(payload), ("127.0.0.1", 9000))
    assert not q.empty()


def test_rtp_receive_ignores_short_packet():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    proto.datagram_received(b"\x00" * 8, ("127.0.0.1", 9000))
    assert q.empty()


def test_rtp_receive_handles_csrc():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
    for _ in range(10):
        proto.datagram_received(build_rtp_packet(payload, cc=2), ("127.0.0.1", 9000))
    assert not q.empty()


def test_rtp_receive_handles_extension():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
    for _ in range(10):
        proto.datagram_received(
            build_rtp_packet(payload, extension=True), ("127.0.0.1", 9000)
        )
    assert not q.empty()


def test_rtp_receive_rejects_non_v2():
    """Packets with RTP version != 2 are silently dropped."""
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    # V=1 packet (0x40 = version 1, no padding/extension/CSRC)
    bad_packet = bytes([0x40, 0x00]) + b"\x00" * 10 + b"\xff" * 160
    for _ in range(10):
        proto.datagram_received(bad_packet, ("127.0.0.1", 9000))
    assert q.empty()


def test_rtp_receive_rejects_wrong_payload_type():
    """Non-PCMU payload types (comfort noise, telephone-event) are dropped."""
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
    for pt in (13, 101):  # CN, telephone-event
        for _ in range(10):
            packet = build_rtp_packet(payload, pt=pt)
            proto.datagram_received(packet, ("127.0.0.1", 9000))
    assert q.empty()


def test_rtp_receive_drops_oldest_on_overflow():
    """When p2d queue is full, oldest frame is dropped and new frame enqueued."""
    q: queue.Queue[bytes] = queue.Queue(maxsize=2)
    old_frame = b"old_frame_marker"
    q.put(old_frame)
    stats = BridgeStats()
    proto = RtpReceiveProtocol(q, stats=stats)
    # Feed enough packets to prime the sinc resampler and overflow
    for _ in range(20):
        proto.datagram_received(build_rtp_packet(b"\xff" * 160), ("127.0.0.1", 9000))
    assert stats.p2d_queue_overflow >= 1
    # Old marker frame should have been dropped
    while not q.empty():
        frame = q.get_nowait()
        assert frame != old_frame
