"""Discord Cog for voice state reconciliation and hangup handling."""

import logging
from typing import Protocol

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)


class CallStateProvider(Protocol):
    def get_bridged_calls(self) -> list[tuple[int, int]]: ...


class HangupHandler(Protocol):
    def hangup_by_voice_channel(self, guild_id: int, channel_id: int) -> None: ...


class PhoneCog(commands.Cog):
    """Reconciles Discord voice state with SIP call state."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        hangup_handler: HangupHandler,
        call_state: CallStateProvider,
    ) -> None:
        self.bot = bot
        self._hangup_handler = hangup_handler
        self._call_state = call_state

    async def cog_load(self) -> None:
        self._reconcile_loop.start()

    async def cog_unload(self) -> None:
        self._reconcile_loop.cancel()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if self.bot.user is None or member.id != self.bot.user.id:
            return
        if before.channel is not None and after.channel is None:
            logger.info(
                "Bot disconnected from voice channel %s, sending BYE",
                before.channel.id,
            )
            self._hangup_handler.hangup_by_voice_channel(
                before.channel.guild.id, before.channel.id
            )

    @tasks.loop(seconds=30)
    async def _reconcile_loop(self) -> None:
        """Detect orphaned SIP calls whose Discord voice client is gone."""
        connected: set[tuple[int, int]] = set()
        for vc in self.bot.voice_clients:
            if not isinstance(vc, discord.VoiceClient):
                continue
            if vc.guild is not None and vc.channel is not None:
                connected.add((vc.guild.id, vc.channel.id))
        bridged = self._call_state.get_bridged_calls()
        for guild_id, channel_id in bridged:
            if (guild_id, channel_id) not in connected:
                logger.warning(
                    "Orphaned call: guild=%s channel=%s "
                    "not in voice_clients, sending BYE",
                    guild_id,
                    channel_id,
                )
                self._hangup_handler.hangup_by_voice_channel(guild_id, channel_id)

    @_reconcile_loop.before_loop
    async def _before_reconcile(self) -> None:
        await self.bot.wait_until_ready()
