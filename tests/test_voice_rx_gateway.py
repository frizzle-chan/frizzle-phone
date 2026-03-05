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
    vc.guild.me.id = 999
    return vc


@pytest.fixture
def ws(voice_client):
    ws = MagicMock()
    ws._connection.voice_client = voice_client
    ws.secret_key = b"\x00" * 32
    return ws


@pytest.mark.asyncio
async def test_ready_maps_own_ssrc(ws, voice_client):
    msg = {"op": READY, "d": {"ssrc": 12345}}
    await hook(ws, msg)
    voice_client._add_ssrc.assert_called_once_with(999, 12345)


@pytest.mark.asyncio
async def test_speaking_maps_user_ssrc(ws, voice_client):
    msg = {"op": SPEAKING, "d": {"user_id": "42", "ssrc": 5678, "speaking": 1}}
    await hook(ws, msg)
    voice_client._add_ssrc.assert_called_once_with(42, 5678)


@pytest.mark.asyncio
async def test_client_disconnect_removes_ssrc(ws, voice_client):
    msg = {"op": CLIENT_DISCONNECT, "d": {"user_id": "42"}}
    await hook(ws, msg)
    voice_client._remove_ssrc.assert_called_once_with(user_id=42)


@pytest.mark.asyncio
async def test_client_disconnect_unknown_user_noop(ws, voice_client):
    """Disconnect for unknown user still calls _remove_ssrc (it handles missing)."""
    msg = {"op": CLIENT_DISCONNECT, "d": {"user_id": "999"}}
    await hook(ws, msg)
    voice_client._remove_ssrc.assert_called_once_with(user_id=999)


@pytest.mark.asyncio
async def test_session_description_updates_key(ws, voice_client):
    msg = {"op": SESSION_DESCRIPTION, "d": {"secret_key": [1, 2, 3]}}
    ws.secret_key = bytes([1, 2, 3] + [0] * 29)
    await hook(ws, msg)
    voice_client._update_secret_key.assert_called_once()
