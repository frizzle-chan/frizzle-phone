# tests/test_voice_rx_client.py
"""Tests for VoiceRecvClient."""

from unittest.mock import MagicMock, patch

from frizzle_phone.discord_voice_rx.client import VoiceRecvClient


class TestVoiceRecvClient:
    def _make_client(self):
        """Create a VoiceRecvClient with mocked discord internals."""
        with patch.object(VoiceRecvClient, "__init__", lambda self, *_a, **_kw: None):
            vc = VoiceRecvClient.__new__(VoiceRecvClient)
            vc._ssrc_to_id = {}
            vc._id_to_ssrc = {}
            vc._decoder_thread = None
            vc._decryptor = None
            vc._connection = MagicMock()
            vc._recv_stats = MagicMock()
            return vc

    def test_add_ssrc(self):
        vc = self._make_client()
        vc._add_ssrc(42, 5678)
        assert vc._ssrc_to_id[5678] == 42
        assert vc._id_to_ssrc[42] == 5678

    def test_remove_ssrc(self):
        vc = self._make_client()
        vc._add_ssrc(42, 5678)
        vc._remove_ssrc(user_id=42)
        assert 5678 not in vc._ssrc_to_id
        assert 42 not in vc._id_to_ssrc

    def test_pop_tick_delegates_to_decoder_thread(self):
        vc = self._make_client()
        vc._decoder_thread = MagicMock()
        vc._decoder_thread.pop_tick.return_value = {1: "frame"}
        assert vc.pop_tick() == {1: "frame"}

    def test_pop_tick_returns_empty_when_not_listening(self):
        vc = self._make_client()
        assert vc.pop_tick() == {}

    def test_socket_callback_tolerates_concurrent_stop(self):
        """Socket callback uses local vars to avoid TOCTOU with stop_listening.

        Simulates stop_listening() nulling _decryptor and _decoder_thread
        during decrypt_rtp(). The local-variable capture ensures decrypt
        completes without AttributeError.
        """
        vc = self._make_client()
        vc._recv_stats = MagicMock(packets_in=0, max_callback_us=0)
        vc._recv_stats.packets_decrypt_failed = 0

        decryptor = MagicMock()
        vc._decryptor = decryptor
        vc._decoder_thread = MagicMock()
        vc._ssrc_to_id = {1234: 42}
        vc._connection.dave_session = None  # No DAVE

        # Build a minimal valid RTP packet (v2, PT=111, seq=1, ts=0, ssrc=1234)
        header = bytes(
            [
                0x80,
                0x6F,  # V=2, P=0, X=0, CC=0, M=0, PT=111
                0x00,
                0x01,  # seq=1
                0x00,
                0x00,
                0x00,
                0x00,  # timestamp=0
                0x00,
                0x00,
                0x04,
                0xD2,  # ssrc=1234
            ]
        )
        packet_data = header + b"\x00" * 20

        def decrypt_and_simulate_stop(*_args, **_kwargs):
            # Simulate stop_listening() running concurrently
            vc._decryptor = None
            vc._decoder_thread = None
            return b"\xf8\xff\xfe"

        decryptor.decrypt_rtp.side_effect = decrypt_and_simulate_stop

        # Should not raise — local var capture means decryptor is still
        # valid even though self._decryptor was set to None
        vc._socket_callback_fn(packet_data)

        # decrypt_rtp was called via local var (would be AttributeError
        # without local capture since self._decryptor is now None)
        decryptor.decrypt_rtp.assert_called_once()
        # No decrypt error was recorded (no exception thrown)
        assert vc._recv_stats.packets_decrypt_failed == 0
