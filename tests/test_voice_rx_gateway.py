# tests/test_voice_rx_gateway.py
"""Tests for discord_voice_rx gateway hook."""

from unittest.mock import MagicMock

import pytest

from frizzle_phone.discord_voice_rx.gateway import hook

# Voice WS opcode constants
READY = 2
SESSION_DESCRIPTION = 4
SPEAKING = 5
CLIENT_DISCONNECT = 13


@pytest.fixture
def voice_client():
    vc = MagicMock()
    vc._ssrc_to_id = {}
    vc._id_to_ssrc = {}
    vc._decoder_thread = MagicMock()
    vc.guild.me.id = 999
    return vc


@pytest.fixture
def ws(voice_client):
    ws = MagicMock()
    ws._connection.voice_client = voice_client
    ws.secret_key = b"\x00" * 32
    ws.READY = READY
    ws.SESSION_DESCRIPTION = SESSION_DESCRIPTION
    return ws


@pytest.mark.asyncio
async def test_ready_maps_own_ssrc(ws, voice_client):
    msg = {"op": READY, "d": {"ssrc": 12345}}
    await hook(ws, msg)
    assert voice_client._ssrc_to_id[12345] == 999
    assert voice_client._id_to_ssrc[999] == 12345


@pytest.mark.asyncio
async def test_speaking_maps_user_ssrc(ws, voice_client):
    msg = {"op": SPEAKING, "d": {"user_id": "42", "ssrc": 5678, "speaking": 1}}
    await hook(ws, msg)
    assert voice_client._ssrc_to_id[5678] == 42
    assert voice_client._id_to_ssrc[42] == 5678
    voice_client._decoder_thread.set_ssrc_user.assert_called_with(5678, 42)


@pytest.mark.asyncio
async def test_client_disconnect_removes_ssrc(ws, voice_client):
    voice_client._ssrc_to_id = {5678: 42}
    voice_client._id_to_ssrc = {42: 5678}
    msg = {"op": CLIENT_DISCONNECT, "d": {"user_id": "42"}}
    await hook(ws, msg)
    assert 5678 not in voice_client._ssrc_to_id
    assert 42 not in voice_client._id_to_ssrc
    voice_client._decoder_thread.destroy_decoder.assert_called_with(
        ssrc=5678, user_id=42
    )


@pytest.mark.asyncio
async def test_client_disconnect_unknown_user_noop(ws, voice_client):
    """Disconnect for unknown user should not crash."""
    msg = {"op": CLIENT_DISCONNECT, "d": {"user_id": "999"}}
    await hook(ws, msg)  # should not raise


@pytest.mark.asyncio
async def test_session_description_updates_key(ws, voice_client):
    voice_client._socket_callback = MagicMock()
    msg = {"op": SESSION_DESCRIPTION, "d": {"secret_key": [1, 2, 3]}}
    ws.secret_key = bytes([1, 2, 3] + [0] * 29)
    await hook(ws, msg)
    voice_client._update_secret_key.assert_called_once()
