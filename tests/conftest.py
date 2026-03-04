"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import sqlite3
import struct
from typing import Any

import aiosqlite
import pytest_asyncio

from frizzle_phone.database import run_migrations


class FakeTransport(asyncio.DatagramTransport):
    """Captures sendto() calls for test assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: Any, addr: Any = None) -> None:
        if addr is not None:
            self.sent.append((bytes(data), addr))


def build_rtp_packet(payload: bytes, *, cc: int = 0, extension: bool = False) -> bytes:
    """Build a minimal RTP packet for testing."""
    first_byte = 0x80 | (0x10 if extension else 0) | cc  # V=2, P=0
    header = struct.pack("!BBHII", first_byte, 0, 0, 0, 0)
    csrc = b"\x00\x00\x00\x00" * cc
    ext_bytes = b""
    if extension:
        ext_bytes = struct.pack("!HH", 0, 1) + b"\x00\x00\x00\x00"  # 1-word extension
    return header + csrc + ext_bytes + payload


# ---------------------------------------------------------------------------
# In-memory SQLite fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = sqlite3.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await run_migrations(conn)
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def seeded_db(db: aiosqlite.Connection) -> aiosqlite.Connection:
    """DB with a seed audio_extensions row for SIP tests."""
    await db.execute(
        "INSERT INTO audio_extensions (extension, audio_name)"
        " VALUES ('frizzle', 'techno')"
    )
    await db.commit()
    return db
