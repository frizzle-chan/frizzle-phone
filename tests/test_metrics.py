"""Tests for Prometheus metrics integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats
from frizzle_phone.metrics import REGISTRY
from frizzle_phone.web import create_app


def _val(name: str) -> float:
    """Read a metric value from the custom registry."""
    v = REGISTRY.get_sample_value(name)
    assert v is not None, f"metric {name!r} not found in registry"
    return v


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
    before_mixed = _val("frizzle_bridge_d2p_frames_mixed_total")
    before_rtp = _val("frizzle_bridge_rtp_frames_sent_total")

    stats = BridgeStats()
    stats.d2p_frames_mixed = 42
    stats.rtp_frames_sent = 100
    stats.log_and_reset()

    assert _val("frizzle_bridge_d2p_frames_mixed_total") - before_mixed == 42
    assert _val("frizzle_bridge_rtp_frames_sent_total") - before_rtp == 100


def test_voice_recv_stats_increments_counters():
    before_packets = _val("frizzle_voice_rx_packets_in_total")
    before_opus = _val("frizzle_voice_rx_opus_decodes_total")

    stats = VoiceRecvStats()
    stats.packets_in = 50
    stats.opus_decodes = 30
    stats.log_and_reset()

    assert _val("frizzle_voice_rx_packets_in_total") - before_packets == 50
    assert _val("frizzle_voice_rx_opus_decodes_total") - before_opus == 30


def test_gauges_are_set_not_accumulated():
    stats = BridgeStats()
    stats.d2p_queue_depth = 10
    stats.rtp_max_sleep_overshoot = 0.005
    stats.log_and_reset()
    assert _val("frizzle_bridge_d2p_queue_depth") == 10
    assert _val("frizzle_bridge_rtp_max_sleep_overshoot_seconds") == 0.005

    # Second call with lower values should replace, not add
    stats.d2p_queue_depth = 3
    stats.rtp_max_sleep_overshoot = 0.001
    stats.log_and_reset()
    assert _val("frizzle_bridge_d2p_queue_depth") == 3
    assert _val("frizzle_bridge_rtp_max_sleep_overshoot_seconds") == 0.001


def test_voice_rx_gauges_are_set_not_accumulated():
    stats = VoiceRecvStats()
    stats.max_callback_us = 500
    stats.log_and_reset()
    assert _val("frizzle_voice_rx_max_callback_microseconds") == 500

    stats.max_callback_us = 200
    stats.log_and_reset()
    assert _val("frizzle_voice_rx_max_callback_microseconds") == 200


def test_zero_counters_not_incremented():
    """Zero-valued counters should not change Prometheus values."""
    before_mixed = _val("frizzle_bridge_d2p_frames_mixed_total")
    stats = BridgeStats()
    # All counters are 0 after init
    stats.log_and_reset()
    assert _val("frizzle_bridge_d2p_frames_mixed_total") == before_mixed


@pytest.mark.asyncio
async def test_metrics_content_type(db):
    app = create_app(db, _make_bot(), [])
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/metrics")
        assert "text/plain" in resp.headers.get("Content-Type", "")
