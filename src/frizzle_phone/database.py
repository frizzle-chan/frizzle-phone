"""Database utilities — migration runner."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_FILENAME_RE = re.compile(r"^(\d+)_.*\.sql$")


async def _ensure_migrations_table(conn: asyncpg.Connection) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            filename    TEXT NOT NULL
        )
    """)


def _discover_migrations() -> list[tuple[int, Path]]:
    """Return (version, path) pairs sorted by version."""
    results: list[tuple[int, Path]] = []
    for p in _MIGRATIONS_DIR.glob("*.sql"):
        m = _FILENAME_RE.match(p.name)
        if m:
            results.append((int(m.group(1)), p))
    results.sort(key=lambda t: t[0])
    return results


async def _get_applied_versions(conn: asyncpg.Connection) -> set[int]:
    rows = await conn.fetch("SELECT version FROM schema_migrations")
    return {r["version"] for r in rows}


async def run_migrations(pool: asyncpg.Pool) -> int:
    """Apply pending migrations and return the number applied."""
    async with pool.acquire() as conn:
        await _ensure_migrations_table(conn)
        applied = await _get_applied_versions(conn)

    migrations = _discover_migrations()
    count = 0
    for version, path in migrations:
        if version in applied:
            continue
        sql = path.read_text().strip()
        if not sql or all(
            line.strip().startswith("--") or not line.strip()
            for line in sql.splitlines()
        ):
            continue
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version, filename) VALUES ($1, $2)",
                version,
                path.name,
            )
        logger.info("Applied migration %s", path.name)
        count += 1

    if count:
        logger.info("Applied %d migration(s)", count)
    else:
        logger.info("No pending migrations")
    return count
