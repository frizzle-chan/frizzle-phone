"""Frizzle-phone SIP server entrypoint."""

import asyncio
import logging

from frizzle_phone.rtp.pcmu import generate_rhythm
from frizzle_phone.sip.server import get_server_ip, start_server

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main() -> None:
    loop = asyncio.get_running_loop()
    server_ip = get_server_ip()
    audio_buf = await loop.run_in_executor(None, generate_rhythm, 60.0)
    transport = await start_server(
        "0.0.0.0", 5060, server_ip=server_ip, audio_buf=audio_buf
    )
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


if __name__ == "__main__":
    asyncio.run(main())
