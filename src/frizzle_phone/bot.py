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
            for call in list(sip_server._calls.values()):
                if (
                    call.voice_client is not None
                    and call.guild_id == before.channel.guild.id
                    and call.channel_id == before.channel.id
                ):
                    logger.info(
                        "Bot disconnected from voice channel %s, sending BYE",
                        before.channel.id,
                    )
                    sip_server._send_bye(call)
                    break

    return bot
