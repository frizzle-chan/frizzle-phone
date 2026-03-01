import queue
import struct

from frizzle_phone.rtp.receive import RtpReceiveProtocol


def _build_rtp_packet(payload: bytes, *, cc: int = 0, extension: bool = False) -> bytes:
    """Build a minimal RTP packet for testing."""
    first_byte = 0x80 | (0x10 if extension else 0) | cc  # V=2, P=0
    header = struct.pack("!BBHII", first_byte, 0, 0, 0, 0)
    csrc = b"\x00\x00\x00\x00" * cc
    ext_bytes = b""
    if extension:
        ext_bytes = struct.pack("!HH", 0, 1) + b"\x00\x00\x00\x00"  # 1-word extension
    return header + csrc + ext_bytes + payload


def test_rtp_receive_extracts_payload():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
    proto.datagram_received(_build_rtp_packet(payload), ("127.0.0.1", 9000))
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
    proto.datagram_received(_build_rtp_packet(payload, cc=2), ("127.0.0.1", 9000))
    assert not q.empty()


def test_rtp_receive_handles_extension():
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q)
    payload = b"\xff" * 160
    proto.datagram_received(
        _build_rtp_packet(payload, extension=True), ("127.0.0.1", 9000)
    )
    assert not q.empty()
