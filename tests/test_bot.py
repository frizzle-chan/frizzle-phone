"""Tests for the Discord bot factory."""

from frizzle_phone.bot import create_bot


def test_create_bot_intents():
    bot = create_bot()
    assert bot.intents.guilds is True
    assert bot.intents.voice_states is True
