# tests/test_voice_rx_rtp.py
"""Tests for discord_voice_rx RTP packet parsing."""

import struct

from frizzle_phone.discord_voice_rx.rtp import (
    OPUS_SILENCE,
    is_rtcp,
    parse_rtp,
)


def _build_rtp(
    *,
    ssrc: int = 1234,
    seq: int = 100,
    timestamp: int = 48000,
    payload: bytes = b"\xaa" * 20,
    marker: bool = False,
    extended: bool = False,
) -> bytes:
    """Build a minimal RTP packet for testing."""
    flags = 0x80  # version=2
    if extended:
        flags |= 0x10
    pt = 0x60 | (0x80 if marker else 0x00)  # payload type 96, marker bit
    header = struct.pack(">BBHII", flags, pt, seq, timestamp, ssrc)
    return header + payload


class TestIsRtcp:
    def test_rtcp_sender_report(self):
        data = bytes([0x80, 200]) + b"\x00" * 10
        assert is_rtcp(data) is True

    def test_rtcp_receiver_report(self):
        data = bytes([0x80, 201]) + b"\x00" * 10
        assert is_rtcp(data) is True

    def test_rtp_not_rtcp(self):
        data = _build_rtp()
        assert is_rtcp(data) is False

    def test_boundary_199_not_rtcp(self):
        data = bytes([0x80, 199]) + b"\x00" * 10
        assert is_rtcp(data) is False

    def test_boundary_205_not_rtcp(self):
        data = bytes([0x80, 205]) + b"\x00" * 10
        assert is_rtcp(data) is False


class TestParseRtp:
    def test_basic_fields(self):
        pkt = parse_rtp(_build_rtp(ssrc=5678, seq=42, timestamp=96000))
        assert pkt.ssrc == 5678
        assert pkt.sequence == 42
        assert pkt.timestamp == 96000
        assert pkt.version == 2

    def test_marker_bit(self):
        pkt = parse_rtp(_build_rtp(marker=True))
        assert pkt.marker is True

    def test_payload_data(self):
        payload = b"\xde\xad\xbe\xef"
        pkt = parse_rtp(_build_rtp(payload=payload))
        assert pkt.data == bytearray(payload)

    def test_header_is_12_bytes(self):
        pkt = parse_rtp(_build_rtp())
        assert len(pkt.header) == 12

    def test_extended_flag(self):
        pkt = parse_rtp(_build_rtp(extended=True))
        assert pkt.extended is True

    def test_silence_detection(self):
        pkt = parse_rtp(_build_rtp(payload=OPUS_SILENCE))
        # Set decrypted_data to opus silence to test is_silence
        pkt.decrypted_data = OPUS_SILENCE
        assert pkt.is_silence() is True

    def test_non_silence(self):
        pkt = parse_rtp(_build_rtp(payload=b"\x01\x02\x03"))
        pkt.decrypted_data = b"\x01\x02\x03"
        assert pkt.is_silence() is False

    def test_no_decrypted_data_not_silence(self):
        pkt = parse_rtp(_build_rtp())
        assert pkt.is_silence() is False

    def test_comparison_by_sequence(self):
        pkt_a = parse_rtp(_build_rtp(ssrc=1, seq=10, timestamp=100))
        pkt_b = parse_rtp(_build_rtp(ssrc=1, seq=20, timestamp=200))
        assert pkt_a < pkt_b

    def test_comparison_wraps_at_65535(self):
        """Sequence 0 should sort AFTER 65535 (wrapping comparison)."""
        old = parse_rtp(_build_rtp(ssrc=1, seq=65535, timestamp=100))
        new = parse_rtp(_build_rtp(ssrc=1, seq=0, timestamp=200))
        assert old < new
        assert not (new < old)

    def test_adjust_rtpsize(self):
        """rtpsize mode: 4-byte nonce at end of data, ext header moved to header."""
        inner_payload = b"\xcc" * 10
        nonce = b"\x01\x02\x03\x04"
        pkt = parse_rtp(_build_rtp(payload=inner_payload + nonce, extended=True))
        pkt.adjust_rtpsize()
        assert pkt.nonce == bytearray(nonce)
