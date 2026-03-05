# tests/test_voice_rx_decrypt.py
"""Tests for discord_voice_rx packet decryption."""

import struct
from unittest.mock import MagicMock

import nacl.exceptions
import nacl.secret
import nacl.utils
import pytest

from frizzle_phone.discord_voice_rx.decrypt import PacketDecryptor, dave_decrypt
from frizzle_phone.discord_voice_rx.rtp import parse_rtp


def _make_secret_key() -> bytes:
    """Generate a valid 32-byte secret key."""
    return nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)


def _make_rtp_header(
    *,
    sequence: int = 1,
    timestamp: int = 480,
    ssrc: int = 12345,
    extended: bool = False,
) -> bytes:
    """Build a minimal 12-byte RTP header (v2, payload type 120)."""
    first_byte = 0x80  # version=2, no padding, no extension, cc=0
    if extended:
        first_byte |= 0x10  # set extension bit
    second_byte = 120  # payload type (Opus)
    return struct.pack(">BBHII", first_byte, second_byte, sequence, timestamp, ssrc)


class TestPacketDecryptorModes:
    """Test that all 4 encryption modes can round-trip encrypt/decrypt."""

    def test_xsalsa20_poly1305(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305", key)
        assert decryptor.mode == "xsalsa20_poly1305"

    def test_xsalsa20_poly1305_lite(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305_lite", key)
        assert decryptor.mode == "xsalsa20_poly1305_lite"

    def test_xsalsa20_poly1305_suffix(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305_suffix", key)
        assert decryptor.mode == "xsalsa20_poly1305_suffix"

    def test_aead_xchacha20_poly1305_rtpsize(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("aead_xchacha20_poly1305_rtpsize", key)
        assert decryptor.mode == "aead_xchacha20_poly1305_rtpsize"

    def test_unsupported_mode_raises(self):
        key = _make_secret_key()
        with pytest.raises(NotImplementedError):
            PacketDecryptor("fake_mode", key)

    def test_update_secret_key(self):
        key1 = _make_secret_key()
        key2 = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305", key1)
        old_box = decryptor._box
        decryptor.update_secret_key(key2)
        assert decryptor._box is not old_box


class TestPacketDecryptorRoundTrip:
    """Round-trip encrypt/decrypt for each mode."""

    PAYLOAD = b"\xf8\xff\xfe" * 10  # 30 bytes of fake Opus data

    def test_xsalsa20_poly1305_roundtrip(self):
        key = _make_secret_key()
        header = _make_rtp_header()
        box = nacl.secret.SecretBox(key)

        # nonce = 12-byte header zero-padded to 24 bytes
        nonce = bytearray(24)
        nonce[:12] = header
        ciphertext = box.encrypt(self.PAYLOAD, bytes(nonce)).ciphertext

        raw_packet = header + ciphertext
        packet = parse_rtp(raw_packet)
        decryptor = PacketDecryptor("xsalsa20_poly1305", key)
        result = decryptor.decrypt_rtp(packet)
        assert result == self.PAYLOAD

    def test_xsalsa20_poly1305_suffix_roundtrip(self):
        key = _make_secret_key()
        header = _make_rtp_header()
        box = nacl.secret.SecretBox(key)

        # nonce = random 24 bytes appended after ciphertext
        nonce = nacl.utils.random(24)
        ciphertext = box.encrypt(self.PAYLOAD, nonce).ciphertext

        raw_packet = header + ciphertext + nonce
        packet = parse_rtp(raw_packet)
        decryptor = PacketDecryptor("xsalsa20_poly1305_suffix", key)
        result = decryptor.decrypt_rtp(packet)
        assert result == self.PAYLOAD

    def test_xsalsa20_poly1305_lite_roundtrip(self):
        key = _make_secret_key()
        header = _make_rtp_header()
        box = nacl.secret.SecretBox(key)

        # nonce = 4-byte counter zero-padded to 24 bytes, 4-byte counter appended
        lite_nonce = struct.pack(">I", 42)
        nonce = bytearray(24)
        nonce[:4] = lite_nonce
        ciphertext = box.encrypt(self.PAYLOAD, bytes(nonce)).ciphertext

        raw_packet = header + ciphertext + lite_nonce
        packet = parse_rtp(raw_packet)
        decryptor = PacketDecryptor("xsalsa20_poly1305_lite", key)
        result = decryptor.decrypt_rtp(packet)
        assert result == self.PAYLOAD

    def test_aead_xchacha20_poly1305_rtpsize_roundtrip(self):
        key = _make_secret_key()
        header = _make_rtp_header()
        aead = nacl.secret.Aead(key)

        # nonce = 4-byte counter zero-padded to 24 bytes
        lite_nonce = struct.pack(">I", 7)
        nonce = bytearray(24)
        nonce[:4] = lite_nonce

        # AEAD uses the header as AAD; .ciphertext strips the prepended nonce
        ciphertext = aead.encrypt(
            self.PAYLOAD, aad=header, nonce=bytes(nonce)
        ).ciphertext

        # raw packet: header + ciphertext + 4-byte nonce
        raw_packet = header + ciphertext + lite_nonce
        packet = parse_rtp(raw_packet)
        decryptor = PacketDecryptor("aead_xchacha20_poly1305_rtpsize", key)
        result = decryptor.decrypt_rtp(packet)
        assert result == self.PAYLOAD

    def test_aead_xchacha20_poly1305_rtpsize_with_extension(self):
        """Round-trip with RTP extension header (extended=True)."""
        key = _make_secret_key()
        header = _make_rtp_header(extended=True)
        aead = nacl.secret.Aead(key)

        lite_nonce = struct.pack(">I", 99)
        nonce = bytearray(24)
        nonce[:4] = lite_nonce

        # Build a minimal BE/DE extension: profile=0xBEDE, length=1 (one 32-bit word)
        # Single element: id=1, len=0 (1 byte), value=0xAB, then 2 pad bytes
        ext_header = b"\xbe\xde\x00\x01"  # profile + length
        ext_body = b"\x10\xab\x00\x00"  # id=1, len=0 (1 byte), value=0xAB, 2-byte pad

        # For rtpsize, adjust_rtpsize() moves the ext_header (4 bytes) into the
        # RTP header and strips the 4-byte nonce from the end.  The resulting
        # packet.header (used as AAD) = original header + ext_header.
        aad = header + ext_header
        plaintext = ext_body + self.PAYLOAD
        ciphertext = aead.encrypt(plaintext, aad=aad, nonce=bytes(nonce)).ciphertext

        # Wire format: header + ext_header + ciphertext + 4-byte nonce
        raw_packet = header + ext_header + ciphertext + lite_nonce
        packet = parse_rtp(raw_packet)
        decryptor = PacketDecryptor("aead_xchacha20_poly1305_rtpsize", key)
        result = decryptor.decrypt_rtp(packet)
        assert result == self.PAYLOAD

    def test_xsalsa20_poly1305_with_extension(self):
        """Round-trip with RTP extension header for xsalsa20 mode."""
        key = _make_secret_key()
        header = _make_rtp_header(extended=True)
        box = nacl.secret.SecretBox(key)

        # Extension header + body are part of the encrypted payload
        ext_header = b"\xbe\xde\x00\x01"
        ext_body = b"\x10\xab\x00\x00"
        plaintext = ext_header + ext_body + self.PAYLOAD

        nonce = bytearray(24)
        nonce[:12] = header
        ciphertext = box.encrypt(plaintext, bytes(nonce)).ciphertext

        raw_packet = header + ciphertext
        packet = parse_rtp(raw_packet)
        decryptor = PacketDecryptor("xsalsa20_poly1305", key)
        result = decryptor.decrypt_rtp(packet)
        assert result == self.PAYLOAD

    def test_wrong_key_fails(self):
        """Decryption with wrong key raises CryptoError."""
        key1 = _make_secret_key()
        key2 = _make_secret_key()
        header = _make_rtp_header()
        box = nacl.secret.SecretBox(key1)

        nonce = bytearray(24)
        nonce[:12] = header
        ciphertext = box.encrypt(self.PAYLOAD, bytes(nonce)).ciphertext

        raw_packet = header + ciphertext
        packet = parse_rtp(raw_packet)
        decryptor = PacketDecryptor("xsalsa20_poly1305", key2)
        with pytest.raises(nacl.exceptions.CryptoError):
            decryptor.decrypt_rtp(packet)


class TestDaveDecrypt:
    def test_dave_decrypts_when_ready(self):
        dave_session = MagicMock()
        dave_session.ready = True
        dave_session.decrypt.return_value = b"decrypted_payload"

        result = dave_decrypt(
            dave_session=dave_session,
            ssrc_to_id={1234: 42},
            ssrc=1234,
            transport_decrypted=b"encrypted_payload",
        )
        assert result == b"decrypted_payload"
        dave_session.decrypt.assert_called_once()

    def test_dave_not_ready_passthrough(self):
        dave_session = MagicMock()
        dave_session.ready = False

        result = dave_decrypt(
            dave_session=dave_session,
            ssrc_to_id={1234: 42},
            ssrc=1234,
            transport_decrypted=b"original",
        )
        assert result == b"original"
        dave_session.decrypt.assert_not_called()

    def test_dave_none_passthrough(self):
        result = dave_decrypt(
            dave_session=None,
            ssrc_to_id={1234: 42},
            ssrc=1234,
            transport_decrypted=b"original",
        )
        assert result == b"original"

    def test_dave_unknown_ssrc_passthrough(self):
        dave_session = MagicMock()
        dave_session.ready = True

        result = dave_decrypt(
            dave_session=dave_session,
            ssrc_to_id={},
            ssrc=9999,
            transport_decrypted=b"original",
        )
        assert result == b"original"
        dave_session.decrypt.assert_not_called()

    def test_dave_decrypt_failure_falls_back(self):
        dave_session = MagicMock()
        dave_session.ready = True
        dave_session.decrypt.side_effect = RuntimeError("DecryptionFailed")

        result = dave_decrypt(
            dave_session=dave_session,
            ssrc_to_id={1234: 42},
            ssrc=1234,
            transport_decrypted=b"original",
        )
        assert result == b"original"
