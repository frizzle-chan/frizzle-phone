"""Tests for database utility functions."""

import asyncpg
import pytest

from frizzle_phone.database import cleanup_stale_calls


@pytest.mark.asyncio
async def test_cleanup_stale_calls(pool: asyncpg.Pool) -> None:
    """Stale ringing/active calls are marked failed; completed calls are untouched."""
    await pool.execute(
        "INSERT INTO calls (sip_call_id, extension, caller_addr, status)"
        " VALUES ('call-1', 'ext1', '10.0.0.1:5060', 'active')"
    )
    await pool.execute(
        "INSERT INTO calls (sip_call_id, extension, caller_addr, status)"
        " VALUES ('call-2', 'ext2', '10.0.0.2:5060', 'ringing')"
    )
    await pool.execute(
        "INSERT INTO calls (sip_call_id, extension, caller_addr, status,"
        " ended_at) VALUES ('call-3', 'ext3', '10.0.0.3:5060', 'completed', now())"
    )

    count = await cleanup_stale_calls(pool)
    assert count == 2

    rows = await pool.fetch(
        "SELECT sip_call_id, status, ended_at FROM calls ORDER BY sip_call_id"
    )
    by_id = {r["sip_call_id"]: r for r in rows}

    assert by_id["call-1"]["status"] == "failed"
    assert by_id["call-1"]["ended_at"] is not None

    assert by_id["call-2"]["status"] == "failed"
    assert by_id["call-2"]["ended_at"] is not None

    assert by_id["call-3"]["status"] == "completed"


@pytest.mark.asyncio
async def test_cleanup_stale_calls_noop(pool: asyncpg.Pool) -> None:
    """Returns 0 when there are no stale calls."""
    count = await cleanup_stale_calls(pool)
    assert count == 0
