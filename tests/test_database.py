"""Tests for database utility functions."""

import uuid

import aiosqlite
import pytest

from frizzle_phone.database import cleanup_stale_calls


@pytest.mark.asyncio
async def test_cleanup_stale_calls(db: aiosqlite.Connection) -> None:
    """Stale ringing/active calls are marked failed; completed calls are untouched."""
    await db.execute(
        "INSERT INTO calls (id, sip_call_id, extension, caller_addr, status)"
        " VALUES (?, 'call-1', 'ext1', '10.0.0.1:5060', 'active')",
        (str(uuid.uuid4()),),
    )
    await db.execute(
        "INSERT INTO calls (id, sip_call_id, extension, caller_addr, status)"
        " VALUES (?, 'call-2', 'ext2', '10.0.0.2:5060', 'ringing')",
        (str(uuid.uuid4()),),
    )
    await db.execute(
        "INSERT INTO calls (id, sip_call_id, extension, caller_addr, status,"
        " ended_at) VALUES (?, 'call-3', 'ext3', '10.0.0.3:5060', 'completed',"
        " datetime('now'))",
        (str(uuid.uuid4()),),
    )
    await db.commit()

    count = await cleanup_stale_calls(db)
    assert count == 2

    cursor = await db.execute(
        "SELECT sip_call_id, status, ended_at FROM calls ORDER BY sip_call_id"
    )
    rows = await cursor.fetchall()
    by_id = {r["sip_call_id"]: r for r in rows}

    assert by_id["call-1"]["status"] == "failed"
    assert by_id["call-1"]["ended_at"] is not None

    assert by_id["call-2"]["status"] == "failed"
    assert by_id["call-2"]["ended_at"] is not None

    assert by_id["call-3"]["status"] == "completed"


@pytest.mark.asyncio
async def test_cleanup_stale_calls_noop(db: aiosqlite.Connection) -> None:
    """Returns 0 when there are no stale calls."""
    count = await cleanup_stale_calls(db)
    assert count == 0
