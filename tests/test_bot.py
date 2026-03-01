"""Tests for the Discord bot factory."""

from unittest.mock import MagicMock

import pytest

from frizzle_phone.bot import create_bot


def test_create_bot_intents():
    bot = create_bot()
    assert bot.intents.guilds is True
    assert bot.intents.voice_states is True


@pytest.mark.asyncio
async def test_voice_state_update_sends_bye_on_bot_disconnect():
    """When bot is disconnected from voice, send BYE for the matching call."""
    bot = create_bot()
    bot._connection.user = MagicMock(id=123)

    mock_call = MagicMock()
    mock_call.voice_client = MagicMock()
    mock_call.guild_id = 1
    mock_call.channel_id = 2

    mock_server = MagicMock()
    mock_server._calls = {"test-call": mock_call}
    bot.sip_server = mock_server  # type: ignore[attr-defined]

    member = MagicMock(id=123)
    before = MagicMock()
    before.channel = MagicMock()
    before.channel.guild.id = 1
    before.channel.id = 2
    after = MagicMock()
    after.channel = None

    # Call the handler directly
    handler = None
    for listener in bot.extra_events.get("on_voice_state_update", []):
        handler = listener
        break
    assert handler is not None, "on_voice_state_update handler not registered"
    await handler(member, before, after)

    mock_server._send_bye.assert_called_once_with(mock_call)


@pytest.mark.asyncio
async def test_voice_state_update_ignores_other_users():
    """Voice state changes from non-bot users are ignored."""
    bot = create_bot()
    bot._connection.user = MagicMock(id=123)

    mock_server = MagicMock()
    mock_server._calls = {}
    bot.sip_server = mock_server  # type: ignore[attr-defined]

    member = MagicMock(id=456)  # different user
    before = MagicMock()
    before.channel = MagicMock()
    after = MagicMock()
    after.channel = None

    handler = None
    for listener in bot.extra_events.get("on_voice_state_update", []):
        handler = listener
        break
    assert handler is not None
    await handler(member, before, after)

    mock_server._send_bye.assert_not_called()
