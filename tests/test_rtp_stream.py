import struct

from frizzle_phone.rtp.stream import build_rtp_packet


def test_rtp_packet_header():
    packet = build_rtp_packet(
        seq=1,
        timestamp=160,
        ssrc=0xDEADBEEF,
        payload=b"\x00" * 160,
    )
    first_byte, second_byte, seq, ts, ssrc = struct.unpack("!BBHII", packet[:12])
    assert (first_byte >> 6) == 2  # RTP version
    assert second_byte == 0  # PT=0 (PCMU), M=0
    assert seq == 1
    assert ts == 160
    assert ssrc == 0xDEADBEEF


def test_rtp_packet_length():
    payload = b"\xff" * 160
    packet = build_rtp_packet(seq=0, timestamp=0, ssrc=0, payload=payload)
    assert len(packet) == 12 + len(payload)


def test_rtp_sequence_increments():
    p1 = build_rtp_packet(seq=42, timestamp=0, ssrc=1, payload=b"\x00")
    p2 = build_rtp_packet(seq=43, timestamp=160, ssrc=1, payload=b"\x00")
    seq1 = struct.unpack("!H", p1[2:4])[0]
    seq2 = struct.unpack("!H", p2[2:4])[0]
    assert seq2 == seq1 + 1


def test_rtp_marker_bit():
    """Marker bit should be set when marker=True."""
    pkt_marked = build_rtp_packet(
        seq=0,
        timestamp=0,
        ssrc=1,
        payload=b"\x00",
        marker=True,
    )
    pkt_normal = build_rtp_packet(
        seq=1,
        timestamp=160,
        ssrc=1,
        payload=b"\x00",
        marker=False,
    )
    second_byte_marked = pkt_marked[1]
    second_byte_normal = pkt_normal[1]
    # Marker bit is the high bit of the second byte
    assert second_byte_marked & 0x80 == 0x80
    assert second_byte_normal & 0x80 == 0
    # Payload type should still be 0 (PCMU) in both
    assert second_byte_marked & 0x7F == 0
    assert second_byte_normal & 0x7F == 0
