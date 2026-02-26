"""Frizzle-phone SIP server entrypoint."""

import asyncio
import logging

from frizzle_phone.sip.server import start_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main() -> None:
    transport = await start_server("0.0.0.0", 5060)
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()


if __name__ == "__main__":
    asyncio.run(main())
