"""Tests for the PhoneCog voice reconciliation cog."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from frizzle_phone.phone_cog import PhoneCog


def _make_cog() -> tuple[PhoneCog, MagicMock, MagicMock]:
    bot = MagicMock()
    bot.user = MagicMock(id=123)
    hangup = MagicMock()
    call_state = MagicMock()
    call_state.get_bridged_calls.return_value = []
    cog = PhoneCog(bot, hangup_handler=hangup, call_state=call_state)
    return cog, hangup, call_state


def _make_voice_client(guild_id: int, channel_id: int) -> MagicMock:
    vc = MagicMock()
    vc.__class__ = discord.VoiceClient
    vc.guild.id = guild_id
    vc.channel.id = channel_id
    return vc


@pytest.mark.asyncio
async def test_voice_state_update_sends_bye_on_bot_disconnect():
    """When bot is disconnected from voice, call hangup_by_voice_channel."""
    cog, hangup, _ = _make_cog()

    member = MagicMock(id=123)
    before = MagicMock()
    before.channel = MagicMock()
    before.channel.guild.id = 1
    before.channel.id = 2
    after = MagicMock()
    after.channel = None

    await cog.on_voice_state_update(member, before, after)

    hangup.hangup_by_voice_channel.assert_called_once_with(1, 2)


@pytest.mark.asyncio
async def test_voice_state_update_ignores_other_users():
    """Voice state changes from non-bot users are ignored."""
    cog, hangup, _ = _make_cog()

    member = MagicMock(id=456)  # different user
    before = MagicMock()
    before.channel = MagicMock()
    after = MagicMock()
    after.channel = None

    await cog.on_voice_state_update(member, before, after)

    hangup.hangup_by_voice_channel.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_detects_orphaned_call():
    """Reconcile sends BYE for a bridged call with no matching voice_client."""
    cog, hangup, call_state = _make_cog()
    cog.bot.voice_clients = []  # type: ignore[assignment]
    call_state.get_bridged_calls.return_value = [(1, 2)]

    await cog._reconcile_loop.coro(cog)

    hangup.hangup_by_voice_channel.assert_called_once_with(1, 2)


@pytest.mark.asyncio
async def test_reconcile_no_false_positives():
    """Reconcile does not hang up when bot is in the correct voice channel."""
    cog, hangup, call_state = _make_cog()

    cog.bot.voice_clients = [_make_voice_client(1, 2)]  # type: ignore[assignment]
    call_state.get_bridged_calls.return_value = [(1, 2)]

    await cog._reconcile_loop.coro(cog)

    hangup.hangup_by_voice_channel.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_multiple_calls_only_orphans_get_bye():
    """Only orphaned calls get BYE; connected calls are left alone."""
    cog, hangup, call_state = _make_cog()

    cog.bot.voice_clients = [_make_voice_client(1, 2)]  # type: ignore[assignment]

    # Two bridged calls: one connected, one orphaned
    call_state.get_bridged_calls.return_value = [(1, 2), (3, 4)]

    await cog._reconcile_loop.coro(cog)

    hangup.hangup_by_voice_channel.assert_called_once_with(3, 4)


@pytest.mark.asyncio
async def test_before_reconcile_waits_until_ready():
    """_before_reconcile calls bot.wait_until_ready()."""
    cog, _, _ = _make_cog()
    cog.bot.wait_until_ready = AsyncMock()  # type: ignore[assignment]

    await cog._before_reconcile()

    cog.bot.wait_until_ready.assert_awaited_once()
