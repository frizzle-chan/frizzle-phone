"""Default VoiceConnector that connects to Discord voice channels."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from frizzle_phone.discord_voice_rx import VoiceRecvClient
from frizzle_phone.voice_protocols import BridgeableVoiceClient

logger = logging.getLogger(__name__)


class DiscordVoiceConnector:
    """Connects to Discord voice channels via bot.get_guild/channel.connect."""

    def __init__(self, bot: commands.Bot) -> None:
        self._bot = bot

    async def connect(self, guild_id: int, channel_id: int) -> BridgeableVoiceClient:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            raise ConnectionError(f"Guild {guild_id} not found")
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            raise ConnectionError(
                f"Voice channel {channel_id} not found in guild {guild_id}"
            )
        vc = await asyncio.wait_for(
            channel.connect(cls=VoiceRecvClient),
            timeout=10.0,
        )
        return vc  # type: ignore[invalid-return-type]  # VoiceRecvClient satisfies BridgeableVoiceClient
