"""Frizzle-phone SIP server entrypoint."""

import asyncio
import logging
import os
import signal

import asyncpg
from dotenv import load_dotenv

from frizzle_phone.bot import create_bot
from frizzle_phone.database import run_migrations
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
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://frizzle_phone:frizzle_phone@localhost:15432/frizzle_phone",
    )

    loop = asyncio.get_running_loop()
    server_ip = get_server_ip()
    silence_prefix = b"\xff" * (SAMPLES_PER_PACKET * 4)

    # Create DB pool and run migrations
    pool = await asyncpg.create_pool(database_url)
    assert pool is not None
    await run_migrations(pool)

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
    bot_task = asyncio.create_task(bot.start(discord_token))
    if discord_token:
        await bot.wait_until_ready()

    # Start SIP server
    transport, server = await start_server(
        "0.0.0.0", 5060, server_ip=server_ip, pool=pool, audio_buffers=audio_buffers
    )

    # Start webapp
    app = create_app(pool, bot, list(audio_buffers.keys()))
    runner = await start_webapp(app, "0.0.0.0", 8080)

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
        bot_task.cancel()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
