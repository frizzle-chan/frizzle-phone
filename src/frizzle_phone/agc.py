"""Per-speaker Automatic Gain Control for the d2p audio path."""

import time

import numpy as np

# Target output level and gain limits (dBFS)
TARGET_DBFS = -20.0
MAX_GAIN_DB = 20.0
MIN_GAIN_DB = -10.0
GATE_DBFS = -50.0

# Time constants (seconds) — attack is slow (boosting quiet),
# release is fast (cutting loud)
ATTACK_TC = 0.5
RELEASE_TC = 0.05
FRAME_DURATION = 0.020  # 20ms

# Smoothing coefficients: alpha = 1 - exp(-frame_dur / tc)
_ATTACK_ALPHA = 1.0 - np.exp(-FRAME_DURATION / ATTACK_TC)
_RELEASE_ALPHA = 1.0 - np.exp(-FRAME_DURATION / RELEASE_TC)

# EMA smoothing for RMS level measurement (~200ms window).
# Prevents gain from tracking syllable-level dynamics in speech.
_LEVEL_TC = 0.200
_LEVEL_ALPHA = 1.0 - np.exp(-FRAME_DURATION / _LEVEL_TC)

# Stale speaker expiry
STALE_TIMEOUT = 30.0


def _rms_dbfs(samples: np.ndarray) -> float:
    """Compute RMS level in dBFS for int16 samples."""
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    if rms < 1.0:
        return -100.0
    return 20.0 * np.log10(rms / 32768.0)


class Agc:
    """RMS-based gain controller for a single speaker."""

    __slots__ = ("gain_db", "_prev_gain_db", "_energy", "last_active")

    def __init__(self) -> None:
        self.gain_db = 0.0
        self._prev_gain_db = 0.0
        self._energy = -1.0  # sentinel: first frame seeds the EMA
        self.last_active = time.monotonic()

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Measure smoothed RMS, update gain, apply with ramp."""
        self.last_active = time.monotonic()
        prev_linear = 10.0 ** (self._prev_gain_db / 20.0)

        # Smoothed level: EMA of frame energy avoids chasing
        # syllable-level dynamics that cause AM artifacts.
        frame_energy = float(np.mean(samples.astype(np.float64) ** 2))
        if self._energy < 0:
            self._energy = frame_energy  # seed on first frame
        else:
            self._energy += _LEVEL_ALPHA * (frame_energy - self._energy)

        rms = np.sqrt(self._energy)
        level = -100.0 if rms < 1.0 else 20.0 * np.log10(rms / 32768.0)

        # Gate: don't adjust gain on silence/noise
        if level >= GATE_DBFS:
            error = TARGET_DBFS - (level + self.gain_db)
            alpha = _ATTACK_ALPHA if error > 0 else _RELEASE_ALPHA
            self.gain_db += alpha * error
            self.gain_db = max(MIN_GAIN_DB, min(MAX_GAIN_DB, self.gain_db))

        # Linear ramp from previous gain to current gain across the
        # frame, eliminating the step discontinuity at frame boundaries.
        cur_linear = 10.0 ** (self.gain_db / 20.0)
        self._prev_gain_db = self.gain_db
        ramp = np.linspace(prev_linear, cur_linear, len(samples))
        amplified = samples.astype(np.float64) * ramp
        return np.clip(amplified, -32768, 32767).astype(np.int16)


class AgcBank:
    """Manages per-speaker Agc instances."""

    def __init__(self) -> None:
        self._speakers: dict[int, Agc] = {}

    def process_slot(self, slot: dict[int, np.ndarray]) -> dict[int, np.ndarray]:
        """Apply per-speaker AGC to a slot of speaker arrays."""
        result: dict[int, np.ndarray] = {}
        for uid, samples in slot.items():
            if uid not in self._speakers:
                self._speakers[uid] = Agc()
            result[uid] = self._speakers[uid].process(samples)
        return result

    def expire_stale(self) -> None:
        """Remove speakers inactive for more than STALE_TIMEOUT."""
        now = time.monotonic()
        stale = [
            uid
            for uid, agc in self._speakers.items()
            if now - agc.last_active > STALE_TIMEOUT
        ]
        for uid in stale:
            del self._speakers[uid]
