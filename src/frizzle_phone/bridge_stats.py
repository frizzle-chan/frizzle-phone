"""Performance counters for the bidirectional audio bridge."""

import logging
import time

logger = logging.getLogger(__name__)


class BridgeStats:
    """Lightweight counters for diagnosing audio bridge performance.

    All fields are plain ints/floats — callers do ``time.monotonic()`` +
    integer increments in the hot path, keeping overhead negligible.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        # Discord → Phone
        self.d2p_frames_in: int = 0
        self.d2p_frames_mixed: int = 0
        self.d2p_queue_overflow: int = 0
        self.d2p_stale_flush: int = 0
        self.d2p_queue_depth_max: int = 0
        self.d2p_max_write_gap: float = 0.0
        self._d2p_last_write: float = 0.0

        # Phone → Discord
        self.p2d_frames_in: int = 0
        self.p2d_queue_overflow: int = 0
        self.p2d_reads: int = 0
        self.p2d_silence_reads: int = 0
        self.p2d_max_recv_gap: float = 0.0
        self._p2d_last_recv: float = 0.0

        # RTP send loop
        self.rtp_frames_sent: int = 0
        self.rtp_silence_sent: int = 0
        self.rtp_max_sleep_overshoot: float = 0.0

    def log_summary(self) -> None:
        """Emit a one-line INFO summary then reset counters."""
        logger.info(
            "bridge stats | d2p in=%d mixed=%d overflow=%d stale=%d "
            "max_gap=%.1fms qmax=%d | p2d in=%d overflow=%d reads=%d "
            "silence=%d max_gap=%.1fms | rtp sent=%d silence=%d "
            "overshoot=%.1fms",
            self.d2p_frames_in,
            self.d2p_frames_mixed,
            self.d2p_queue_overflow,
            self.d2p_stale_flush,
            self.d2p_max_write_gap * 1000,
            self.d2p_queue_depth_max,
            self.p2d_frames_in,
            self.p2d_queue_overflow,
            self.p2d_reads,
            self.p2d_silence_reads,
            self.p2d_max_recv_gap * 1000,
            self.rtp_frames_sent,
            self.rtp_silence_sent,
            self.rtp_max_sleep_overshoot * 1000,
        )

        if self.d2p_queue_overflow > 0:
            logger.warning(
                "bridge d2p queue overflow: %d frames dropped", self.d2p_queue_overflow
            )

        if self.p2d_reads > 0 and self.p2d_silence_reads / self.p2d_reads > 0.20:
            logger.warning(
                "bridge p2d underflow: %d/%d reads were silence (%.0f%%)",
                self.p2d_silence_reads,
                self.p2d_reads,
                self.p2d_silence_reads / self.p2d_reads * 100,
            )

        if (
            self.rtp_frames_sent > 0
            and self.rtp_silence_sent / self.rtp_frames_sent > 0.20
        ):
            logger.warning(
                "bridge d2p starvation: %d/%d RTP sends were silence (%.0f%%)",
                self.rtp_silence_sent,
                self.rtp_frames_sent,
                self.rtp_silence_sent / self.rtp_frames_sent * 100,
            )

        self.reset()

    def record_d2p_write(self) -> None:
        """Call from PhoneAudioSink.write() hot path."""
        self.d2p_frames_in += 1
        now = time.monotonic()
        if self._d2p_last_write > 0:
            gap = now - self._d2p_last_write
            if gap > self.d2p_max_write_gap:
                self.d2p_max_write_gap = gap
            if gap > 0.040:
                logger.warning("bridge d2p write gap: %.1fms", gap * 1000)
        self._d2p_last_write = now

    def record_p2d_recv(self) -> None:
        """Call from RtpReceiveProtocol.datagram_received() hot path."""
        self.p2d_frames_in += 1
        now = time.monotonic()
        if self._p2d_last_recv > 0:
            gap = now - self._p2d_last_recv
            if gap > self.p2d_max_recv_gap:
                self.p2d_max_recv_gap = gap
            if gap > 0.040:
                logger.warning("bridge p2d recv gap: %.1fms", gap * 1000)
        self._p2d_last_recv = now
