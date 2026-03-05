"""Frizzle-phone SIP server entrypoint."""

import argparse
import asyncio
import logging
import os
import signal
import sqlite3
from pathlib import Path

import aiosqlite
import discord
from dotenv import load_dotenv

from frizzle_phone.bot import create_bot
from frizzle_phone.database import cleanup_stale_calls, run_migrations
from frizzle_phone.phone_cog import PhoneCog
from frizzle_phone.rtp.pcmu import pcm_to_ulaw
from frizzle_phone.rtp.stream import SAMPLES_PER_PACKET
from frizzle_phone.sip.server import get_server_ip, start_server
from frizzle_phone.synth import generate_beeps_pcm, generate_rhythm_pcm
from frizzle_phone.web import create_app, start_webapp, stop_webapp

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()
    discord_token = os.environ.get("DISCORD_TOKEN", "")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db", default=os.environ.get("DATABASE_PATH", "frizzle-phone.db")
    )
    args = parser.parse_args()

    loop = asyncio.get_running_loop()
    server_ip = get_server_ip()
    silence_prefix = b"\xff" * (SAMPLES_PER_PACKET * 4)

    # Create DB connection and run migrations
    db = await aiosqlite.connect(args.db)
    db.row_factory = sqlite3.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await run_migrations(db)
    await cleanup_stale_calls(db)

    # Create Discord bot
    bot = create_bot()

    # Pre-render audio buffers keyed by name
    techno_buf = await loop.run_in_executor(
        None, lambda: pcm_to_ulaw(generate_rhythm_pcm(60.0))
    )
    techno_buf = silence_prefix + techno_buf

    beeps_buf = await loop.run_in_executor(
        None, lambda: pcm_to_ulaw(generate_beeps_pcm())
    )
    beeps_buf = silence_prefix + beeps_buf

    audio_buffers = {"techno": techno_buf, "beeps": beeps_buf}

    # Start Discord bot
    if discord_token:
        await bot.login(discord_token)
        bot_task = asyncio.create_task(bot.connect())
        await bot.wait_until_ready()

        # Disconnect stale voice clients from previous run — the gateway may
        # restore them on reconnect even though we have no matching SIP call.
        for vc in list(bot.voice_clients):
            if isinstance(vc, discord.VoiceClient):
                logger.warning(
                    "Disconnecting stale voice client: guild=%s channel=%s",
                    vc.guild.id if vc.guild else None,
                    vc.channel.id if vc.channel else None,
                )
            await vc.disconnect(force=True)
    else:
        bot_task = None

    sip_port = int(os.environ.get("SIP_PORT", "5060"))
    web_port = int(os.environ.get("WEB_PORT", "8080"))

    # Start SIP server
    transport, server = await start_server(
        "0.0.0.0",
        sip_port,
        server_ip=server_ip,
        db=db,
        audio_buffers=audio_buffers,
        bot=bot,
    )
    # Register PhoneCog for voice-disconnect events and reconciliation
    await bot.add_cog(PhoneCog(bot, hangup_handler=server, call_state=server))

    # Start webapp
    app = create_app(db, bot, list(audio_buffers.keys()))
    runner = await start_webapp(app, "0.0.0.0", web_port)

    shutdown = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    try:
        await shutdown.wait()
        logger.info("Shutting down...")
    finally:
        server.graceful_shutdown()
        transport.close()
        await stop_webapp(runner)
        if not bot.is_closed():
            await bot.close()
        if bot_task is not None:
            bot_task.cancel()
        await db.close()


def cli() -> None:
    """Console script entry point."""
    PID_FILE = Path("frizzle-phone.pid")
    PID_FILE.write_text(str(os.getpid()))
    asyncio.run(main())


if __name__ == "__main__":
    cli()
