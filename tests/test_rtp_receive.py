import queue

from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.receive import RtpReceiveProtocol
from tests.conftest import build_rtp_packet


def test_rtp_receive_extracts_payload():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
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
    proto.datagram_received(build_rtp_packet(payload, cc=2), ("127.0.0.1", 9000))
    assert not q.empty()


def test_rtp_receive_handles_extension():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
    proto.datagram_received(
        build_rtp_packet(payload, extension=True), ("127.0.0.1", 9000)
    )
    assert not q.empty()


def test_rtp_receive_drops_oldest_on_overflow():
    """When p2d queue is full, oldest frame is dropped and new frame enqueued."""
    q: queue.Queue[bytes] = queue.Queue(maxsize=1)
    old_frame = b"old_frame_marker"
    q.put(old_frame)
    stats = BridgeStats()
    proto = RtpReceiveProtocol(q, stats=stats)
    proto.datagram_received(build_rtp_packet(b"\xff" * 160), ("127.0.0.1", 9000))
    assert stats.p2d_queue_overflow == 1
    assert q.qsize() == 1
    frame = q.get_nowait()
    assert frame != old_frame  # old was dropped, new was enqueued
