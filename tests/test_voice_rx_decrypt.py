# tests/test_voice_rx_decrypt.py
"""Tests for discord_voice_rx packet decryption."""

from unittest.mock import MagicMock

import nacl.secret
import nacl.utils
import pytest

from frizzle_phone.discord_voice_rx.decrypt import PacketDecryptor, dave_decrypt


def _make_secret_key() -> bytes:
    """Generate a valid 32-byte secret key."""
    return nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)


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
