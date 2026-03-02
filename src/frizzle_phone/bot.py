"""Discord bot factory — provides guild/channel enumeration for the webapp."""

import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


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
            sip_server = getattr(bot, "sip_server", None)
            if sip_server is None:
                return
            logger.info(
                "Bot disconnected from voice channel %s, sending BYE",
                before.channel.id,
            )
            sip_server.hangup_by_voice_channel(
                before.channel.guild.id, before.channel.id
            )

    return bot
