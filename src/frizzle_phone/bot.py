"""Discord bot factory — provides guild/channel enumeration for the webapp."""

import logging
from typing import Protocol

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class HangupHandler(Protocol):
    def hangup_by_voice_channel(self, guild_id: int, channel_id: int) -> None: ...


_hangup_handler: HangupHandler | None = None


def set_hangup_handler(handler: HangupHandler) -> None:
    """Register the hangup handler (called from main after server creation)."""
    global _hangup_handler  # noqa: PLW0603
    _hangup_handler = handler


def clear_hangup_handler() -> None:
    """Remove the hangup handler (useful for test teardown)."""
    global _hangup_handler  # noqa: PLW0603
    _hangup_handler = None


def create_bot() -> commands.Bot:
    """Create a Bot with guilds + voice_states intents (no commands)."""
    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.listen("on_voice_state_update")
    async def on_voice_state_update(
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if bot.user is None or member.id != bot.user.id:
            return
        if before.channel is not None and after.channel is None:
            if _hangup_handler is None:
                return
            logger.info(
                "Bot disconnected from voice channel %s, sending BYE",
                before.channel.id,
            )
            _hangup_handler.hangup_by_voice_channel(
                before.channel.guild.id, before.channel.id
            )

    return bot
