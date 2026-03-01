-- Extension routing tables and call log

CREATE TABLE discord_extensions (
    extension TEXT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audio_extensions (
    extension TEXT PRIMARY KEY,
    audio_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sip_call_id TEXT NOT NULL,
    extension TEXT NOT NULL,
    caller_addr TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ringing',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    guild_id BIGINT,
    channel_id BIGINT
);

-- Enforce one active discord call per phone at the DB level
SET lock_timeout = '1s';
SET statement_timeout = '5s';
CREATE UNIQUE INDEX idx_one_active_discord_call_per_phone
    ON calls (caller_addr)
    WHERE status IN ('ringing', 'active') AND guild_id IS NOT NULL;
SET lock_timeout = '0';
SET statement_timeout = '0';
