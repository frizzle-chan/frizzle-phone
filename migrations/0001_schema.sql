-- Extension routing tables and call log (SQLite)

CREATE TABLE discord_extensions (
    extension TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE audio_extensions (
    extension TEXT PRIMARY KEY,
    audio_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE calls (
    id TEXT PRIMARY KEY,
    sip_call_id TEXT NOT NULL,
    extension TEXT NOT NULL,
    caller_addr TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ringing',
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    answered_at TEXT,
    ended_at TEXT,
    guild_id INTEGER,
    channel_id INTEGER
);

-- Enforce one active discord call per phone at the DB level
CREATE UNIQUE INDEX idx_one_active_discord_call_per_phone
    ON calls (caller_addr)
    WHERE status IN ('ringing', 'active') AND guild_id IS NOT NULL;
