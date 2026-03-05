# tests/test_voice_rx_stats.py
"""Tests for VoiceRecvStats."""

import logging

from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats


def test_initial_counters_zero():
    stats = VoiceRecvStats()
    assert stats.packets_in == 0
    assert stats.opus_decodes == 0
    assert stats.ticks_empty == 0
    assert stats.max_callback_us == 0


def test_reset_clears_counters():
    stats = VoiceRecvStats()
    stats.packets_in = 100
    stats.opus_decodes = 50
    stats.max_decode_us = 500
    stats.reset()
    assert stats.packets_in == 0
    assert stats.opus_decodes == 0
    assert stats.max_decode_us == 0


def test_log_and_reset_emits_log(caplog):
    stats = VoiceRecvStats()
    stats.packets_in = 10
    stats.opus_decodes = 8
    with caplog.at_level(logging.INFO, logger="frizzle_phone.discord_voice_rx.stats"):
        stats.log_and_reset()
    assert any("voice_recv stats" in r.message for r in caplog.records)
    assert stats.packets_in == 0


def test_maybe_log_respects_interval():
    stats = VoiceRecvStats()
    stats.packets_in = 5
    # First call should not log (interval not elapsed)
    stats.maybe_log_and_reset()
    # Counter should still be set (interval hasn't elapsed)
    assert stats.packets_in == 5


def test_decrypt_failed_warning(caplog):
    stats = VoiceRecvStats()
    stats.packets_in = 100
    stats.packets_decrypt_failed = 10
    logger = "frizzle_phone.discord_voice_rx.stats"
    with caplog.at_level(logging.WARNING, logger=logger):
        stats.log_and_reset()
    assert any("decrypt" in r.message.lower() for r in caplog.records)
