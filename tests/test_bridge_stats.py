import logging
import queue
from unittest.mock import patch

from frizzle_phone.bridge import PhoneAudioSource
from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.receive import RtpReceiveProtocol
from tests.conftest import build_rtp_packet

# ---------------------------------------------------------------------------
# BridgeStats unit tests
# ---------------------------------------------------------------------------


def test_bridge_stats_reset():
    stats = BridgeStats()
    stats.d2p_frames_mixed = 10
    stats.p2d_frames_in = 5
    stats.rtp_frames_sent = 20
    stats._p2d_last_recv = 5678.0
    stats.reset()
    assert stats.d2p_frames_mixed == 0
    assert stats.p2d_frames_in == 0
    assert stats.rtp_frames_sent == 0
    assert stats._p2d_last_recv == 0.0


def test_log_summary_emits_info(caplog):
    stats = BridgeStats()
    stats.d2p_frames_mixed = 250
    stats.p2d_frames_in = 200
    stats.rtp_frames_sent = 250
    with caplog.at_level(logging.INFO, logger="frizzle_phone.bridge_stats"):
        stats.log_and_reset()
    assert any("bridge stats" in r.message for r in caplog.records)
    assert any("d2p mixed=250" in r.message for r in caplog.records)


def test_log_summary_resets_counters():
    stats = BridgeStats()
    stats.d2p_frames_mixed = 100
    stats.p2d_reads = 100
    stats.rtp_frames_sent = 100
    stats.log_and_reset()
    assert stats.d2p_frames_mixed == 0
    assert stats.p2d_reads == 0
    assert stats.rtp_frames_sent == 0


def test_log_summary_warns_on_d2p_freshness_drops(caplog):
    stats = BridgeStats()
    stats.d2p_frames_dropped = 3
    stats.rtp_frames_sent = 100
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_and_reset()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("d2p freshness drops" in r.message for r in warnings)


def test_log_summary_warns_on_high_silence_reads(caplog):
    stats = BridgeStats()
    stats.p2d_reads = 100
    stats.p2d_silence_reads = 30  # 30% > 20%
    stats.rtp_frames_sent = 100
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_and_reset()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("p2d underflow" in r.message for r in warnings)


def test_log_summary_warns_on_high_silence_sends(caplog):
    stats = BridgeStats()
    stats.rtp_frames_sent = 100
    stats.rtp_silence_sent = 25  # 25% > 20%
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_and_reset()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("d2p starvation" in r.message for r in warnings)


def test_log_summary_no_warn_when_below_thresholds(caplog):
    stats = BridgeStats()
    stats.rtp_frames_sent = 100
    stats.rtp_silence_sent = 10  # 10% < 20%
    stats.p2d_reads = 100
    stats.p2d_silence_reads = 10  # 10% < 20%
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_and_reset()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0


def test_maybe_log_and_reset_skips_before_interval():
    """maybe_log_and_reset() does nothing before the summary interval elapses."""
    stats = BridgeStats()
    stats.d2p_frames_mixed = 100
    stats._last_summary = 1000.0
    with patch("frizzle_phone.bridge_stats.time") as mock_time:
        mock_time.monotonic.return_value = 1002.0  # only 2s elapsed
        stats.maybe_log_and_reset()
    assert stats.d2p_frames_mixed == 100  # not reset


def test_maybe_log_and_reset_logs_after_interval(caplog):
    """maybe_log_and_reset() logs and resets after interval elapses."""
    stats = BridgeStats()
    stats.d2p_frames_mixed = 100
    stats.rtp_frames_sent = 50
    stats._last_summary = 1000.0
    with (
        patch("frizzle_phone.bridge_stats.time") as mock_time,
        caplog.at_level(logging.INFO, logger="frizzle_phone.bridge_stats"),
    ):
        mock_time.monotonic.return_value = 1006.0  # 6s elapsed > 5s interval
        stats.maybe_log_and_reset()
    assert stats.d2p_frames_mixed == 0  # was reset
    assert any("bridge stats" in r.message for r in caplog.records)


def test_p2d_gap_warnings_emitted_in_summary(caplog):
    """Accumulated p2d gap warnings appear in the log_and_reset() summary."""
    stats = BridgeStats()
    stats._p2d_gap_warnings = 3
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.bridge_stats"):
        stats.log_and_reset()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("p2d recv gaps" in r.message and "3" in r.message for r in warnings)


def test_reset_clears_gap_warnings():
    """reset() zeros gap warning counters."""
    stats = BridgeStats()
    stats._p2d_gap_warnings = 7
    stats.reset()
    assert stats._p2d_gap_warnings == 0


# ---------------------------------------------------------------------------
# Integration: PhoneAudioSource with stats
# ---------------------------------------------------------------------------


def test_source_read_increments_p2d_reads():
    stats = BridgeStats()
    q: queue.Queue[bytes] = queue.Queue()
    source = PhoneAudioSource(q, stats=stats)
    source.read()
    assert stats.p2d_reads == 1
    assert stats.p2d_silence_reads == 1


def test_source_read_with_data_no_silence():
    stats = BridgeStats()
    q: queue.Queue[bytes] = queue.Queue()
    q.put(b"\x42" * 3840)
    source = PhoneAudioSource(q, stats=stats)
    source.read()
    assert stats.p2d_reads == 1
    assert stats.p2d_silence_reads == 0


# ---------------------------------------------------------------------------
# Integration: RtpReceiveProtocol with stats
# ---------------------------------------------------------------------------


def test_rtp_receive_increments_p2d_frames_in():
    stats = BridgeStats()
    q: queue.Queue[bytes] = queue.Queue()
    proto = RtpReceiveProtocol(q, stats=stats)
    payload = b"\xff" * 160
    proto.datagram_received(build_rtp_packet(payload), ("127.0.0.1", 9000))
    assert stats.p2d_frames_in == 1
