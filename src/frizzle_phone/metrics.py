"""Prometheus metrics for frizzle-phone.

Uses a custom CollectorRegistry (not the default global) so tests stay
isolated and the /metrics endpoint only exposes our own metrics.
"""

from __future__ import annotations

from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge

REGISTRY = CollectorRegistry()

# Callbacks invoked at scrape time to refresh gauges that are expensive
# or error-prone to keep in sync via mutation-point tracking.
_scrape_callbacks: list[Callable[[], None]] = []


def register_scrape_callback(fn: Callable[[], None]) -> None:
    _scrape_callbacks.append(fn)


def run_scrape_callbacks() -> None:
    for fn in _scrape_callbacks:
        fn()


# ---------------------------------------------------------------------------
# Bridge counters (incremented by each 5s snapshot delta)
# ---------------------------------------------------------------------------

BRIDGE_D2P_MIXED = Counter(
    "frizzle_bridge_d2p_frames_mixed_total",
    "Discord-to-phone frames mixed",
    registry=REGISTRY,
)
BRIDGE_D2P_DROPPED = Counter(
    "frizzle_bridge_d2p_frames_dropped_total",
    "Discord-to-phone frames dropped (freshness)",
    registry=REGISTRY,
)
BRIDGE_P2D_IN = Counter(
    "frizzle_bridge_p2d_frames_in_total",
    "Phone-to-Discord RTP frames received",
    registry=REGISTRY,
)
BRIDGE_P2D_OVERFLOW = Counter(
    "frizzle_bridge_p2d_queue_overflow_total",
    "Phone-to-Discord queue overflows",
    registry=REGISTRY,
)
BRIDGE_P2D_READS = Counter(
    "frizzle_bridge_p2d_reads_total",
    "Phone-to-Discord read() calls",
    registry=REGISTRY,
)
BRIDGE_P2D_SILENCE = Counter(
    "frizzle_bridge_p2d_silence_reads_total",
    "Phone-to-Discord silence reads",
    registry=REGISTRY,
)
BRIDGE_P2D_GAP_WARNS = Counter(
    "frizzle_bridge_p2d_gap_warnings_total",
    "Phone-to-Discord recv gap warnings (>40ms)",
    registry=REGISTRY,
)
BRIDGE_RTP_SENT = Counter(
    "frizzle_bridge_rtp_frames_sent_total",
    "RTP frames sent to phone",
    registry=REGISTRY,
)
BRIDGE_RTP_SILENCE = Counter(
    "frizzle_bridge_rtp_silence_sent_total",
    "RTP silence frames sent to phone",
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Bridge gauges (set to peak/current value each summary period)
# ---------------------------------------------------------------------------

BRIDGE_D2P_QDEPTH = Gauge(
    "frizzle_bridge_d2p_queue_depth",
    "Discord-to-phone slot queue depth at snapshot",
    registry=REGISTRY,
)
BRIDGE_P2D_MAX_GAP = Gauge(
    "frizzle_bridge_p2d_max_recv_gap_seconds",
    "Max phone-to-Discord recv gap (seconds)",
    registry=REGISTRY,
)
BRIDGE_RTP_OVERSHOOT = Gauge(
    "frizzle_bridge_rtp_max_sleep_overshoot_seconds",
    "Max RTP send loop sleep overshoot (seconds)",
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Voice receive counters
# ---------------------------------------------------------------------------

VOICE_RX_PACKETS_IN = Counter(
    "frizzle_voice_rx_packets_in_total",
    "Voice receive packets in",
    registry=REGISTRY,
)
VOICE_RX_DECRYPT_FAIL = Counter(
    "frizzle_voice_rx_decrypt_failures_total",
    "Voice receive decrypt failures",
    registry=REGISTRY,
)
VOICE_RX_OPUS_DECODES = Counter(
    "frizzle_voice_rx_opus_decodes_total",
    "Voice receive Opus decodes",
    registry=REGISTRY,
)
VOICE_RX_OPUS_ERRORS = Counter(
    "frizzle_voice_rx_opus_errors_total",
    "Voice receive Opus errors",
    registry=REGISTRY,
)
VOICE_RX_TICKS_EMPTY = Counter(
    "frizzle_voice_rx_ticks_empty_total",
    "Voice receive empty ticks",
    registry=REGISTRY,
)
VOICE_RX_TICKS_SERVED = Counter(
    "frizzle_voice_rx_ticks_served_total",
    "Voice receive served ticks",
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Voice receive gauges
# ---------------------------------------------------------------------------

VOICE_RX_MAX_CALLBACK_US = Gauge(
    "frizzle_voice_rx_max_callback_microseconds",
    "Voice receive max callback duration (microseconds)",
    registry=REGISTRY,
)
VOICE_RX_MAX_DECODE_US = Gauge(
    "frizzle_voice_rx_max_decode_microseconds",
    "Voice receive max decode duration (microseconds)",
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# SIP server gauge
# ---------------------------------------------------------------------------

ACTIVE_CALLS = Gauge(
    "frizzle_active_calls",
    "Number of active SIP calls",
    registry=REGISTRY,
)
