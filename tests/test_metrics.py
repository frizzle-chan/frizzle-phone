"""Tests for Prometheus metrics integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats
from frizzle_phone.metrics import (
    BRIDGE_D2P_MIXED,
    BRIDGE_D2P_QDEPTH,
    BRIDGE_RTP_OVERSHOOT,
    BRIDGE_RTP_SENT,
    VOICE_RX_MAX_CALLBACK_US,
    VOICE_RX_OPUS_DECODES,
    VOICE_RX_PACKETS_IN,
)
from frizzle_phone.web import create_app


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.guilds = []
    return bot


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200(db):
    app = create_app(db, _make_bot(), [])
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/metrics")
        assert resp.status == 200
        text = await resp.text()
        assert "frizzle_bridge_d2p_frames_mixed_total" in text
        assert "frizzle_voice_rx_packets_in_total" in text
        assert "frizzle_active_calls" in text


def test_bridge_stats_increments_counters():
    before_mixed = BRIDGE_D2P_MIXED._value.get()
    before_rtp = BRIDGE_RTP_SENT._value.get()

    stats = BridgeStats()
    stats.d2p_frames_mixed = 42
    stats.rtp_frames_sent = 100
    stats.log_and_reset()

    assert BRIDGE_D2P_MIXED._value.get() - before_mixed == 42
    assert BRIDGE_RTP_SENT._value.get() - before_rtp == 100


def test_voice_recv_stats_increments_counters():
    before_packets = VOICE_RX_PACKETS_IN._value.get()
    before_opus = VOICE_RX_OPUS_DECODES._value.get()

    stats = VoiceRecvStats()
    stats.packets_in = 50
    stats.opus_decodes = 30
    stats.log_and_reset()

    assert VOICE_RX_PACKETS_IN._value.get() - before_packets == 50
    assert VOICE_RX_OPUS_DECODES._value.get() - before_opus == 30


def test_gauges_are_set_not_accumulated():
    stats = BridgeStats()
    stats.d2p_queue_depth = 10
    stats.rtp_max_sleep_overshoot = 0.005
    stats.log_and_reset()
    assert BRIDGE_D2P_QDEPTH._value.get() == 10
    assert BRIDGE_RTP_OVERSHOOT._value.get() == 0.005

    # Second call with lower values should replace, not add
    stats.d2p_queue_depth = 3
    stats.rtp_max_sleep_overshoot = 0.001
    stats.log_and_reset()
    assert BRIDGE_D2P_QDEPTH._value.get() == 3
    assert BRIDGE_RTP_OVERSHOOT._value.get() == 0.001


def test_voice_rx_gauges_are_set_not_accumulated():
    stats = VoiceRecvStats()
    stats.max_callback_us = 500
    stats.log_and_reset()
    assert VOICE_RX_MAX_CALLBACK_US._value.get() == 500

    stats.max_callback_us = 200
    stats.log_and_reset()
    assert VOICE_RX_MAX_CALLBACK_US._value.get() == 200


def test_zero_counters_not_incremented():
    """Counter.inc(0) raises ValueError — verify we guard against it."""
    before_mixed = BRIDGE_D2P_MIXED._value.get()
    stats = BridgeStats()
    # All counters are 0 after init
    stats.log_and_reset()
    assert BRIDGE_D2P_MIXED._value.get() == before_mixed


@pytest.mark.asyncio
async def test_metrics_content_type(db):
    app = create_app(db, _make_bot(), [])
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/metrics")
        assert "text/plain" in resp.headers.get("Content-Type", "")
