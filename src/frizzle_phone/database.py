"""Database utilities — migration runner."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_FILENAME_RE = re.compile(r"^(\d+)_.*\.sql$")


async def _ensure_migrations_table(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now')),
            filename    TEXT NOT NULL
        )
    """)
    await db.commit()


def _discover_migrations() -> list[tuple[int, Path]]:
    """Return (version, path) pairs sorted by version."""
    results: list[tuple[int, Path]] = []
    for p in _MIGRATIONS_DIR.glob("*.sql"):
        m = _FILENAME_RE.match(p.name)
        if m:
            results.append((int(m.group(1)), p))
    results.sort(key=lambda t: t[0])
    return results


async def _get_applied_versions(db: aiosqlite.Connection) -> set[int]:
    cursor = await db.execute("SELECT version FROM schema_migrations")
    rows = await cursor.fetchall()
    return {r[0] for r in rows}


async def run_migrations(db: aiosqlite.Connection) -> int:
    """Apply pending migrations and return the number applied."""
    await _ensure_migrations_table(db)
    applied = await _get_applied_versions(db)

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
        await db.execute("BEGIN")
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                await db.execute(stmt)
        await db.execute(
            "INSERT INTO schema_migrations (version, filename) VALUES (?, ?)",
            (version, path.name),
        )
        await db.commit()
        logger.info("Applied migration %s", path.name)
        count += 1

    if count:
        logger.info("Applied %d migration(s)", count)
    else:
        logger.info("No pending migrations")
    return count


async def cleanup_stale_calls(db: aiosqlite.Connection) -> int:
    """Mark any ringing/active calls as failed (stale from prior crash)."""
    cursor = await db.execute(
        "UPDATE calls SET status = 'failed', ended_at = datetime('now')"
        " WHERE status IN ('ringing', 'active')"
    )
    count = cursor.rowcount
    if count:
        await db.commit()
        logger.warning("Cleaned up %d stale call(s) from previous run", count)
    return count
