"""Performance counters for the Discord voice receive pipeline."""

import logging
import time

from frizzle_phone import metrics

logger = logging.getLogger(__name__)

_SUMMARY_INTERVAL_S = 5.0


class VoiceRecvStats:
    """Lightweight counters for diagnosing voice receive performance.

    All fields are plain ints. Callers do ``time.monotonic()`` +
    integer increments in the hot path, keeping overhead negligible.
    """

    def __init__(self) -> None:
        self._last_summary: float = time.monotonic()
        self.reset()

    def reset(self) -> None:
        self.packets_in: int = 0
        self.packets_decrypt_failed: int = 0
        self.opus_decodes: int = 0
        self.opus_errors: int = 0
        self.ticks_empty: int = 0
        self.ticks_served: int = 0
        self.max_callback_us: int = 0
        self.max_decode_us: int = 0

    def log_and_reset(self) -> None:
        """Snapshot counters, reset, then log the snapshot."""
        snap = {
            "packets_in": self.packets_in,
            "decrypt_fail": self.packets_decrypt_failed,
            "opus_decodes": self.opus_decodes,
            "opus_errors": self.opus_errors,
            "ticks_empty": self.ticks_empty,
            "ticks_served": self.ticks_served,
            "max_callback_us": self.max_callback_us,
            "max_decode_us": self.max_decode_us,
        }

        self.reset()

        # Feed snapshot into Prometheus counters/gauges
        if snap["packets_in"]:
            metrics.VOICE_RX_PACKETS_IN.inc(snap["packets_in"])
        if snap["decrypt_fail"]:
            metrics.VOICE_RX_DECRYPT_FAIL.inc(snap["decrypt_fail"])
        if snap["opus_decodes"]:
            metrics.VOICE_RX_OPUS_DECODES.inc(snap["opus_decodes"])
        if snap["opus_errors"]:
            metrics.VOICE_RX_OPUS_ERRORS.inc(snap["opus_errors"])
        if snap["ticks_empty"]:
            metrics.VOICE_RX_TICKS_EMPTY.inc(snap["ticks_empty"])
        if snap["ticks_served"]:
            metrics.VOICE_RX_TICKS_SERVED.inc(snap["ticks_served"])
        metrics.VOICE_RX_MAX_CALLBACK_US.set(snap["max_callback_us"])
        metrics.VOICE_RX_MAX_DECODE_US.set(snap["max_decode_us"])

        logger.info(
            "voice_recv stats | pkts_in=%d decrypt_fail=%d opus=%d/%d "
            "ticks=%d/%d cb_max=%dus decode_max=%dus",
            snap["packets_in"],
            snap["decrypt_fail"],
            snap["opus_decodes"],
            snap["opus_errors"],
            snap["ticks_served"],
            snap["ticks_empty"],
            snap["max_callback_us"],
            snap["max_decode_us"],
        )

        if snap["decrypt_fail"] > 0 and snap["packets_in"] > 0:
            logger.warning(
                "voice_recv decrypt failures: %d/%d packets (%.0f%%)",
                snap["decrypt_fail"],
                snap["packets_in"],
                snap["decrypt_fail"] / snap["packets_in"] * 100,
            )

    def maybe_log_and_reset(self) -> None:
        """Log summary if the summary interval has elapsed."""
        now = time.monotonic()
        if now - self._last_summary >= _SUMMARY_INTERVAL_S:
            self.log_and_reset()
            self._last_summary = now
