"""Tests for the Discord bot factory."""

from unittest.mock import MagicMock

import pytest

from frizzle_phone.bot import clear_hangup_handler, create_bot, set_hangup_handler


@pytest.fixture(autouse=True)
def _cleanup_hangup_handler():
    yield
    clear_hangup_handler()


def test_create_bot_intents():
    bot = create_bot()
    assert bot.intents.guilds is True
    assert bot.intents.voice_states is True


@pytest.mark.asyncio
async def test_voice_state_update_sends_bye_on_bot_disconnect():
    """When bot is disconnected from voice, call hangup_by_voice_channel."""
    bot = create_bot()
    bot._connection.user = MagicMock(id=123)

    mock_server = MagicMock()
    set_hangup_handler(mock_server)

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

    mock_server.hangup_by_voice_channel.assert_called_once_with(1, 2)


@pytest.mark.asyncio
async def test_voice_state_update_ignores_other_users():
    """Voice state changes from non-bot users are ignored."""
    bot = create_bot()
    bot._connection.user = MagicMock(id=123)

    mock_server = MagicMock()
    set_hangup_handler(mock_server)

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

    mock_server.hangup_by_voice_channel.assert_not_called()
