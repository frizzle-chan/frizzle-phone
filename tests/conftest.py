"""Shared test fixtures."""

import asyncio
from typing import Any


class FakeTransport(asyncio.DatagramTransport):
    """Captures sendto() calls for test assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: Any, addr: Any = None) -> None:
        if addr is not None:
            self.sent.append((bytes(data), addr))
