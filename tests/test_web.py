"""Tests for the extension management webapp."""

from __future__ import annotations

from unittest.mock import MagicMock

import asyncpg
import pytest
from aiohttp.test_utils import TestClient, TestServer

from frizzle_phone.web import create_app


def _make_bot(guilds: list[dict] | None = None) -> MagicMock:
    """Create a fake bot with mock guilds and voice channels."""
    bot = MagicMock()
    if guilds is None:
        guilds = [
            {
                "id": 111,
                "name": "Test Guild",
                "voice_channels": [
                    {"id": 1001, "name": "General"},
                    {"id": 1002, "name": "Music"},
                ],
            }
        ]
    mock_guilds = []
    for g in guilds:
        guild = MagicMock()
        guild.id = g["id"]
        guild.name = g["name"]
        channels = []
        for ch in g["voice_channels"]:
            channel = MagicMock()
            channel.id = ch["id"]
            channel.name = ch["name"]
            channels.append(channel)
        guild.voice_channels = channels
        mock_guilds.append(guild)
    bot.guilds = mock_guilds
    return bot


@pytest.mark.asyncio
async def test_get_renders_form(pool: asyncpg.Pool):
    bot = _make_bot()
    app = create_app(pool, bot, ["techno", "beeps"])
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "Extension Routing" in text
        assert "Test Guild" in text
        assert "General" in text
        assert "techno" in text
        assert "beeps" in text


@pytest.mark.asyncio
async def test_post_saves_extensions(pool: asyncpg.Pool):
    bot = _make_bot()
    app = create_app(pool, bot, ["techno", "beeps"])
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/extensions",
            data={
                "discord_111_1001": "100",
                "audio_techno": "200",
            },
            allow_redirects=False,
        )
        assert resp.status == 303

        # Verify data in DB
        discord_row = await pool.fetchrow(
            "SELECT * FROM discord_extensions WHERE extension = '100'"
        )
        assert discord_row is not None
        assert discord_row["guild_id"] == 111
        assert discord_row["channel_id"] == 1001

        audio_row = await pool.fetchrow(
            "SELECT * FROM audio_extensions WHERE extension = '200'"
        )
        assert audio_row is not None
        assert audio_row["audio_name"] == "techno"


@pytest.mark.asyncio
async def test_post_duplicate_extension_returns_400(pool: asyncpg.Pool):
    bot = _make_bot()
    app = create_app(pool, bot, ["techno"])
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/extensions",
            data={
                "discord_111_1001": "100",
                "audio_techno": "100",  # same extension
            },
            allow_redirects=False,
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_post_clears_extensions(pool: asyncpg.Pool):
    # Seed some data first
    await pool.execute(
        "INSERT INTO audio_extensions (extension, audio_name) VALUES ('old', 'techno')"
    )
    bot = _make_bot()
    app = create_app(pool, bot, ["techno"])
    async with TestClient(TestServer(app)) as client:
        # Post with empty values — clears all extensions
        resp = await client.post(
            "/extensions",
            data={"audio_techno": ""},
            allow_redirects=False,
        )
        assert resp.status == 303

        count = await pool.fetchval("SELECT count(*) FROM audio_extensions")
        assert count == 0


@pytest.mark.asyncio
async def test_get_shows_existing_extensions(pool: asyncpg.Pool):
    await pool.execute(
        "INSERT INTO discord_extensions (extension, guild_id, channel_id)"
        " VALUES ('500', 111, 1001)"
    )
    bot = _make_bot()
    app = create_app(pool, bot, ["techno"])
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        text = await resp.text()
        assert "500" in text
