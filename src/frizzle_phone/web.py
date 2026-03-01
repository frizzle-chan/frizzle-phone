"""Extension management webapp — aiohttp-based."""

from __future__ import annotations

from typing import Any

import aiohttp_jinja2
import asyncpg
import jinja2
from aiohttp import web
from discord.ext import commands

_pool_key = web.AppKey("pool", asyncpg.Pool)
_bot_key = web.AppKey("bot", commands.Bot)
_audio_names_key = web.AppKey("audio_names", list)


async def _get_handler(
    request: web.Request,
) -> web.Response:
    pool = request.app[_pool_key]
    bot = request.app[_bot_key]
    audio_names = request.app[_audio_names_key]

    discord_rows = await pool.fetch(
        "SELECT extension, guild_id, channel_id FROM discord_extensions"
    )
    audio_rows = await pool.fetch("SELECT extension, audio_name FROM audio_extensions")

    discord_map: dict[str, dict[str, Any]] = {}
    for row in discord_rows:
        key = f"{row['guild_id']}_{row['channel_id']}"
        discord_map[key] = {"extension": row["extension"]}

    audio_map: dict[str, str] = {}
    for row in audio_rows:
        audio_map[row["audio_name"]] = row["extension"]

    context = {
        "guilds": bot.guilds,
        "discord_map": discord_map,
        "audio_names": audio_names,
        "audio_map": audio_map,
    }
    return aiohttp_jinja2.render_template("extensions.html", request, context)


async def _post_handler(
    request: web.Request,
) -> web.Response:
    pool = request.app[_pool_key]
    data = await request.post()

    discord_entries: list[tuple[str, int, int]] = []
    audio_entries: list[tuple[str, str]] = []

    for key, value in data.items():
        ext = str(value).strip()
        if not ext:
            continue
        if key.startswith("discord_"):
            parts = key.split("_", 2)
            if len(parts) == 3:
                guild_id = int(parts[1])
                channel_id = int(parts[2])
                discord_entries.append((ext, guild_id, channel_id))
        elif key.startswith("audio_"):
            audio_name = key[len("audio_") :]
            audio_entries.append((ext, audio_name))

    # Check cross-table uniqueness
    all_extensions = [e[0] for e in discord_entries] + [e[0] for e in audio_entries]
    if len(all_extensions) != len(set(all_extensions)):
        return web.Response(status=400, text="Duplicate extension across tables")

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM discord_extensions")
        await conn.execute("DELETE FROM audio_extensions")
        for ext, guild_id, channel_id in discord_entries:
            await conn.execute(
                "INSERT INTO discord_extensions (extension, guild_id, channel_id)"
                " VALUES ($1, $2, $3)",
                ext,
                guild_id,
                channel_id,
            )
        for ext, audio_name in audio_entries:
            await conn.execute(
                "INSERT INTO audio_extensions (extension, audio_name) VALUES ($1, $2)",
                ext,
                audio_name,
            )

    raise web.HTTPSeeOther(location="/")


def create_app(
    pool: asyncpg.Pool,
    bot: commands.Bot,
    audio_names: list[str],
) -> web.Application:
    app = web.Application()
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.PackageLoader("frizzle_phone"),
        autoescape=jinja2.select_autoescape(),
    )
    app[_pool_key] = pool
    app[_bot_key] = bot
    app[_audio_names_key] = audio_names
    app.router.add_get("/", _get_handler)
    app.router.add_post("/extensions", _post_handler)
    return app


async def start_webapp(app: web.Application, host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner


async def stop_webapp(runner: web.AppRunner) -> None:
    await runner.cleanup()
