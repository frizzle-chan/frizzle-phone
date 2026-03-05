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
