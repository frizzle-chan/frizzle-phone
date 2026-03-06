"""Performance counters for the bidirectional audio bridge.

Callers use ``if self._stats:`` guards rather than a null-object pattern.
This keeps the hot path zero-overhead when stats are disabled and avoids
the complexity of a NullBridgeStats class whose no-op methods would still
incur call overhead on every frame.
"""

import logging
import time

from frizzle_phone import metrics

logger = logging.getLogger(__name__)

_SUMMARY_INTERVAL_S = 5.0


class BridgeStats:
    """Lightweight counters for diagnosing audio bridge performance.

    All fields are plain ints/floats. Callers do ``time.monotonic()`` +
    integer increments in the hot path, keeping overhead negligible.
    """

    def __init__(self) -> None:
        self._last_summary: float = 0.0
        self.reset()

    def reset(self) -> None:
        # Discord → Phone
        self.d2p_frames_mixed: int = 0
        self.d2p_frames_dropped: int = 0
        self.d2p_queue_depth: int = 0

        # Phone → Discord
        self.p2d_frames_in: int = 0
        self.p2d_queue_overflow: int = 0
        self.p2d_reads: int = 0
        self.p2d_silence_reads: int = 0
        self.p2d_max_recv_gap: float = 0.0
        self._p2d_last_recv: float = 0.0
        self._p2d_gap_warnings: int = 0

        # RTP send loop
        self.rtp_frames_sent: int = 0
        self.rtp_silence_sent: int = 0
        self.rtp_max_sleep_overshoot: float = 0.0

    def log_and_reset(self) -> None:
        """Snapshot counters, reset, then log the snapshot.

        Resets before logging so other threads write to fresh counters
        while this method formats the log message from local copies.
        """
        # Snapshot all counters atomically (under GIL, the reads happen
        # back-to-back before any interleaving writer can see reset values)
        snap = {
            "d2p_mixed": self.d2p_frames_mixed,
            "d2p_dropped": self.d2p_frames_dropped,
            "d2p_qdepth": self.d2p_queue_depth,
            "p2d_in": self.p2d_frames_in,
            "p2d_overflow": self.p2d_queue_overflow,
            "p2d_reads": self.p2d_reads,
            "p2d_silence": self.p2d_silence_reads,
            "p2d_max_gap": self.p2d_max_recv_gap,
            "p2d_gap_warns": self._p2d_gap_warnings,
            "rtp_sent": self.rtp_frames_sent,
            "rtp_silence": self.rtp_silence_sent,
            "rtp_overshoot": self.rtp_max_sleep_overshoot,
        }

        self.reset()

        # Feed snapshot into Prometheus counters/gauges
        if snap["d2p_mixed"]:
            metrics.BRIDGE_D2P_MIXED.inc(snap["d2p_mixed"])
        if snap["d2p_dropped"]:
            metrics.BRIDGE_D2P_DROPPED.inc(snap["d2p_dropped"])
        if snap["p2d_in"]:
            metrics.BRIDGE_P2D_IN.inc(snap["p2d_in"])
        if snap["p2d_overflow"]:
            metrics.BRIDGE_P2D_OVERFLOW.inc(snap["p2d_overflow"])
        if snap["p2d_reads"]:
            metrics.BRIDGE_P2D_READS.inc(snap["p2d_reads"])
        if snap["p2d_silence"]:
            metrics.BRIDGE_P2D_SILENCE.inc(snap["p2d_silence"])
        if snap["p2d_gap_warns"]:
            metrics.BRIDGE_P2D_GAP_WARNS.inc(snap["p2d_gap_warns"])
        if snap["rtp_sent"]:
            metrics.BRIDGE_RTP_SENT.inc(snap["rtp_sent"])
        if snap["rtp_silence"]:
            metrics.BRIDGE_RTP_SILENCE.inc(snap["rtp_silence"])
        metrics.BRIDGE_D2P_QDEPTH.set(snap["d2p_qdepth"])
        metrics.BRIDGE_P2D_MAX_GAP.set(snap["p2d_max_gap"])
        metrics.BRIDGE_RTP_OVERSHOOT.set(snap["rtp_overshoot"])

        logger.info(
            "bridge stats | d2p mixed=%d dropped=%d "
            "qdepth=%d | p2d in=%d overflow=%d reads=%d "
            "silence=%d max_gap=%.1fms | rtp sent=%d silence=%d "
            "overshoot=%.1fms",
            snap["d2p_mixed"],
            snap["d2p_dropped"],
            snap["d2p_qdepth"],
            snap["p2d_in"],
            snap["p2d_overflow"],
            snap["p2d_reads"],
            snap["p2d_silence"],
            snap["p2d_max_gap"] * 1000,
            snap["rtp_sent"],
            snap["rtp_silence"],
            snap["rtp_overshoot"] * 1000,
        )

        if snap["d2p_dropped"] > 0:
            logger.warning(
                "bridge d2p freshness drops: %d slots discarded", snap["d2p_dropped"]
            )

        if snap["p2d_reads"] > 0 and snap["p2d_silence"] / snap["p2d_reads"] > 0.20:
            logger.warning(
                "bridge p2d underflow: %d/%d reads were silence (%.0f%%)",
                snap["p2d_silence"],
                snap["p2d_reads"],
                snap["p2d_silence"] / snap["p2d_reads"] * 100,
            )

        if snap["rtp_sent"] > 0:
            expected_silence = max(0, snap["rtp_sent"] - snap["d2p_mixed"])
            unexplained = max(0, snap["rtp_silence"] - expected_silence)
            if unexplained / snap["rtp_sent"] > 0.10:
                audio_sent = snap["rtp_sent"] - snap["rtp_silence"]
                logger.warning(
                    "bridge d2p pipeline loss: fed %d mixed slots but only %d "
                    "audio payloads sent (%d/%d unexplained silence, %.0f%%)",
                    snap["d2p_mixed"],
                    audio_sent,
                    unexplained,
                    snap["rtp_sent"],
                    unexplained / snap["rtp_sent"] * 100,
                )

        if snap["p2d_gap_warns"] > 0:
            logger.warning(
                "bridge p2d recv gaps >40ms: %d occurrences", snap["p2d_gap_warns"]
            )

    def maybe_log_and_reset(self) -> None:
        """Log summary if the summary interval has elapsed."""
        now = time.monotonic()
        if now - self._last_summary >= _SUMMARY_INTERVAL_S:
            self.log_and_reset()
            self._last_summary = now

    def record_p2d_recv(self) -> None:
        """Call from RtpReceiveProtocol.datagram_received() hot path."""
        self.p2d_frames_in += 1
        now = time.monotonic()
        if self._p2d_last_recv > 0:
            gap = now - self._p2d_last_recv
            if gap > self.p2d_max_recv_gap:
                self.p2d_max_recv_gap = gap
            if gap > 0.040:
                self._p2d_gap_warnings += 1
        self._p2d_last_recv = now
