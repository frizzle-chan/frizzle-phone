"""Shared test fixtures."""

from __future__ import annotations

import asyncio
import glob
import os
import random
import string
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio


class FakeTransport(asyncio.DatagramTransport):
    """Captures sendto() calls for test assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: Any, addr: Any = None) -> None:
        if addr is not None:
            self.sent.append((bytes(data), addr))


# ---------------------------------------------------------------------------
# Real ephemeral database fixtures
# ---------------------------------------------------------------------------

_DEFAULT_DB_URL = (
    "postgresql://frizzle_phone:frizzle_phone@localhost:5432/frizzle_phone"
)


def _db_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)


def _parse_dsn(url: str) -> dict[str, Any]:
    """Extract host/port/user/password from a PostgreSQL URL."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "frizzle_phone",
        "password": parsed.password or "frizzle_phone",
        "database": parsed.path.lstrip("/") or "frizzle_phone",
    }


def _connect_kwargs(dsn: dict[str, Any], database: str | None = None) -> dict[str, Any]:
    """Build asyncpg connection kwargs with SSL disabled."""
    return {
        "host": dsn["host"],
        "port": dsn["port"],
        "user": dsn["user"],
        "password": dsn["password"],
        "database": database or dsn["database"],
        "ssl": False,
    }


@pytest.fixture(scope="session")
def _test_db_name() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"frizzle_phone_test_{suffix}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _test_db(_test_db_name: str) -> Any:
    """Create a temporary database, run migrations, yield name, drop it."""
    dsn = _parse_dsn(_db_url())
    admin_conn = await asyncpg.connect(**_connect_kwargs(dsn))
    try:
        await admin_conn.execute(f'CREATE DATABASE "{_test_db_name}"')
    finally:
        await admin_conn.close()

    # Run migrations
    migration_conn = await asyncpg.connect(
        **_connect_kwargs(dsn, database=_test_db_name)
    )
    try:
        migration_files = sorted(glob.glob("migrations/*.sql"))
        for mf in migration_files:
            with open(mf) as f:
                sql = f.read().strip()
            if sql and not all(
                line.strip().startswith("--") or not line.strip()
                for line in sql.splitlines()
            ):
                await migration_conn.execute(sql)
    finally:
        await migration_conn.close()

    yield _test_db_name

    # Drop the test database
    drop_conn = await asyncpg.connect(**_connect_kwargs(dsn))
    try:
        await drop_conn.execute(f'DROP DATABASE IF EXISTS "{_test_db_name}"')
    finally:
        await drop_conn.close()


@pytest_asyncio.fixture
async def pool(_test_db: str, _test_db_name: str) -> Any:
    """Function-scoped asyncpg pool connected to the ephemeral test DB."""
    dsn = _parse_dsn(_db_url())
    p = await asyncpg.create_pool(
        **_connect_kwargs(dsn, database=_test_db_name),
        min_size=1,
        max_size=5,
    )
    assert p is not None
    # Clean tables before each test
    async with p.acquire() as conn:
        await conn.execute("DELETE FROM calls")
        await conn.execute("DELETE FROM discord_extensions")
        await conn.execute("DELETE FROM audio_extensions")
    yield p
    await p.close()


@pytest_asyncio.fixture
async def seeded_pool(pool: asyncpg.Pool) -> asyncpg.Pool:
    """Pool with a seed audio_extensions row for SIP tests."""
    await pool.execute(
        "INSERT INTO audio_extensions (extension, audio_name)"
        " VALUES ('frizzle', 'techno')"
    )
    return pool
