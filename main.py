"""Frizzle-phone SIP server entrypoint."""

import asyncio
import logging
import signal

from frizzle_phone.rtp.pcmu import pcm_to_ulaw
from frizzle_phone.rtp.stream import SAMPLES_PER_PACKET
from frizzle_phone.sip.server import get_server_ip, start_server
from frizzle_phone.synth import generate_rhythm_pcm

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    loop = asyncio.get_running_loop()
    server_ip = get_server_ip()
    audio_buf = await loop.run_in_executor(
        None, lambda: pcm_to_ulaw(generate_rhythm_pcm(60.0))
    )
    # Prepend 80ms of µ-law silence so the phone's jitter buffer can fill
    # before meaningful audio begins (avoids first-call distortion)
    audio_buf = b"\xff" * (SAMPLES_PER_PACKET * 4) + audio_buf
    transport, server = await start_server(
        "0.0.0.0", 5060, server_ip=server_ip, audio_buf=audio_buf
    )
    shutdown = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    try:
        await shutdown.wait()
        logger.info("Shutting down...")
    finally:
        server.graceful_shutdown()
        transport.close()


if __name__ == "__main__":
    asyncio.run(main())
