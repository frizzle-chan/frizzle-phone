"""Unit + integration tests for per-speaker AGC."""

import time
from unittest.mock import patch

import numpy as np

from frizzle_phone.agc import (
    MAX_GAIN_DB,
    MIN_GAIN_DB,
    TARGET_DBFS,
    Agc,
    AgcBank,
    _rms_dbfs,
    _soft_clip,
)
from frizzle_phone.bridge import mix_slot

# 960 samples = 20ms at 48kHz mono
FRAME_LEN = 960


def _tone(dbfs: float, n: int = FRAME_LEN) -> np.ndarray:
    """Generate a sine tone at a given RMS dBFS level (int16).

    For a sine wave, RMS = amplitude / sqrt(2), so we scale amplitude
    by sqrt(2) to achieve the desired RMS level.
    """
    rms_linear = 32768.0 * 10.0 ** (dbfs / 20.0)
    amplitude = rms_linear * np.sqrt(2.0)
    t = np.arange(n, dtype=np.float64)
    # 1kHz at 48kHz sample rate
    signal = amplitude * np.sin(2.0 * np.pi * 1000.0 * t / 48000.0)
    return np.clip(signal, -32768, 32767).astype(np.int16)


def _measure_dbfs(samples: np.ndarray) -> float:
    return _rms_dbfs(samples)


def _run_frames(agc: Agc, dbfs: float, count: int) -> np.ndarray:
    """Feed `count` frames at `dbfs` through agc, return last output."""
    tone = _tone(dbfs)
    out = tone
    for _ in range(count):
        out = agc.process(tone)
    return out


def test_quiet_signal_boosted():
    """Quiet signal (-40 dBFS) gets boosted toward -20 dBFS over ~2s."""
    agc = Agc()
    # 2s = 100 frames at 20ms
    out = _run_frames(agc, -40.0, 100)
    out_level = _measure_dbfs(out)
    # Should be within 3 dB of target
    assert out_level > TARGET_DBFS - 3.0, (
        f"Output {out_level:.1f} dBFS not boosted enough"
    )


def test_loud_signal_attenuated():
    """Loud signal (-10 dBFS) gets attenuated toward -20 dBFS within ~500ms."""
    agc = Agc()
    # 500ms = 25 frames; level estimate converges slower with 500ms TC
    out = _run_frames(agc, -10.0, 25)
    out_level = _measure_dbfs(out)
    # Should be within 3 dB of target
    assert out_level < TARGET_DBFS + 3.0, (
        f"Output {out_level:.1f} dBFS not attenuated enough"
    )


def test_gate_no_gain_increase_on_silence():
    """Near-silence (-55 dBFS) does not increase gain."""
    agc = Agc()
    _run_frames(agc, -55.0, 50)
    assert agc.gain_db <= 0.1, f"Gain increased to {agc.gain_db:.1f} dB on gated signal"


def test_gate_preserves_existing_gain():
    """Existing gain still applied during gated frames (no jump on resume)."""
    agc = Agc()
    # Build up some gain with a quiet-but-above-gate signal
    _run_frames(agc, -35.0, 50)
    gain_before = agc.gain_db
    assert gain_before > 1.0, "Should have positive gain"

    # Feed gated signal long enough for smoothed level to decay below gate.
    # The EMA takes several frames to drop, so gain may drift slightly
    # before the gate fully engages — that's expected with smoothed level.
    _run_frames(agc, -55.0, 50)
    # Gain should still be positive (not reset to 0)
    assert agc.gain_db > 1.0, "Gain should persist through gated period"

    # Gain is still applied — output should be louder than input
    quiet_tone = _tone(-55.0)
    out = agc.process(quiet_tone)
    out_level = _measure_dbfs(out)
    in_level = _measure_dbfs(quiet_tone)
    assert out_level > in_level + 0.5, "Gain not applied during gated frame"


def test_max_gain_clamped():
    """Max gain clamped at +20 dB."""
    agc = Agc()
    # Very quiet signal, many frames — gain should max out
    _run_frames(agc, -45.0, 500)
    assert agc.gain_db <= MAX_GAIN_DB + 0.01, f"Gain {agc.gain_db:.1f} exceeded max"


def test_min_gain_clamped():
    """Min gain clamped at -10 dB."""
    agc = Agc()
    # Very loud signal
    _run_frames(agc, 0.0, 500)
    assert agc.gain_db >= MIN_GAIN_DB - 0.01, f"Gain {agc.gain_db:.1f} below min"


def test_no_int16_overflow():
    """Output clipped correctly — no values outside int16 range."""
    agc = Agc()
    # Pump gain up first with quiet signal
    _run_frames(agc, -35.0, 100)
    # Then feed a loud signal before gain can adapt
    loud = _tone(-3.0)
    out = agc.process(loud)
    assert out.dtype == np.int16
    assert np.all(out >= -32768)
    assert np.all(out <= 32767)


def test_soft_clip_gradient():
    """Soft limiter output near clipping is smooth (no hard edges)."""
    agc = Agc()
    # Pump gain up with quiet signal
    _run_frames(agc, -35.0, 100)
    assert agc.gain_db > 5.0, "Need positive gain for this test"

    # Feed loud signal — high gain + loud input should hit limiter
    loud = _tone(-3.0)
    out = agc.process(loud)
    assert out.dtype == np.int16
    # With tanh soft clip, no output samples should be at exactly ±32767
    # (hard clip produces flat edges; tanh asymptotically approaches but never reaches)
    assert not np.any(np.abs(out) == 32767), (
        "Soft limiter should not produce samples at exactly ±32767"
    )
    # Output should still be loud (not collapsed)
    assert _measure_dbfs(out) > -10.0


def test_holdoff_blocks_brief_burst():
    """Gain doesn't increase for brief above-gate bursts (<6 frames)."""
    agc = Agc()
    # Feed 4 frames of quiet-but-above-gate signal (needs gain boost)
    for _ in range(4):
        agc.process(_tone(-35.0))
    assert agc.gain_db == 0.0, (
        f"Gain should not increase during hold-off, got {agc.gain_db:.2f}"
    )

    # Reset with silence to clear the counter
    for _ in range(10):
        agc.process(_tone(-55.0))

    # Feed 8+ continuous above-gate frames — gain should increase
    agc2 = Agc()
    for _ in range(10):
        agc2.process(_tone(-35.0))
    assert agc2.gain_db > 0.0, (
        f"Gain should increase after hold-off, got {agc2.gain_db:.2f}"
    )


def test_holdoff_does_not_block_attenuation():
    """Gain decreases are never held off — loud signals attenuated immediately."""
    agc = Agc()
    # First frame of a loud signal should still decrease gain
    agc.process(_tone(-5.0))
    assert agc.gain_db < 0.0, (
        f"Attenuation should not be held off, got {agc.gain_db:.2f}"
    )


def test_soft_clip_passthrough():
    """Soft limiter is transparent for small signals."""
    small = np.array([0, 100, -100, 1000, -1000], dtype=np.int16)
    out = _soft_clip(small.astype(np.float64))
    # For small values, tanh(x) ≈ x — output should be very close to input
    np.testing.assert_allclose(
        out.astype(np.float64),
        small.astype(np.float64),
        atol=2.0,
    )


def test_attack_slower_than_release():
    """Attack (gain increase) is slower than release (gain decrease)."""
    # Measure frames to reach within 3 dB of target from each direction
    agc_attack = Agc()
    for i in range(500):
        out = agc_attack.process(_tone(-40.0))
        if _measure_dbfs(out) > TARGET_DBFS - 3.0:
            attack_frames = i
            break
    else:
        attack_frames = 500

    agc_release = Agc()
    for i in range(500):
        out = agc_release.process(_tone(-10.0))
        if _measure_dbfs(out) < TARGET_DBFS + 3.0:
            release_frames = i
            break
    else:
        release_frames = 500

    assert attack_frames > release_frames, (
        f"Attack ({attack_frames} frames) should be slower "
        f"than release ({release_frames} frames)"
    )


def test_signal_at_target_stays_stable():
    """Signal already at -20 dBFS: gain stays near 0 dB."""
    agc = Agc()
    _run_frames(agc, TARGET_DBFS, 100)
    assert abs(agc.gain_db) < 1.0, (
        f"Gain drifted to {agc.gain_db:.1f} dB at target level"
    )


def test_output_dtype_always_int16():
    """Output dtype always int16."""
    agc = Agc()
    for dbfs in [-40.0, -20.0, -5.0]:
        out = agc.process(_tone(dbfs))
        assert out.dtype == np.int16


def test_agcbank_independent_per_speaker():
    """AgcBank creates independent per-speaker state."""
    bank = AgcBank()
    # Need >6 frames to get past hold-off for gain increase
    for _ in range(10):
        slot = {1: _tone(-40.0), 2: _tone(-5.0)}
        bank.process_slot(slot)
    # Speaker 1 should have positive gain, speaker 2 negative
    assert bank._speakers[1].gain_db > 0
    assert bank._speakers[2].gain_db < 0


def test_agcbank_persists_state():
    """AgcBank persists state across slots."""
    bank = AgcBank()
    for _ in range(50):
        bank.process_slot({1: _tone(-40.0)})
    gain_after_50 = bank._speakers[1].gain_db
    assert gain_after_50 > 2.0, "Gain should have accumulated"

    # Continue feeding — gain should keep increasing
    for _ in range(50):
        bank.process_slot({1: _tone(-40.0)})
    assert bank._speakers[1].gain_db > gain_after_50


def test_agcbank_expire_stale():
    """AgcBank expire_stale removes old entries."""
    bank = AgcBank()
    bank.process_slot({1: _tone(-20.0), 2: _tone(-20.0)})
    assert len(bank._speakers) == 2

    # Patch time to make speaker 1 stale
    with patch("frizzle_phone.agc.time") as mock_time:
        mock_time.monotonic.return_value = time.monotonic() + 60.0
        bank.expire_stale()

    assert len(bank._speakers) == 0


def test_integration_agcbank_mix_balanced():
    """Quiet + loud speaker through AgcBank -> mix_slot gives balanced output."""
    bank = AgcBank()
    # Converge AGC states over 2s
    # Use -30 (needs +10 dB, within max) and -10 (needs -10 dB, within min)
    for _ in range(100):
        slot = {1: _tone(-30.0), 2: _tone(-10.0)}
        result = bank.process_slot(slot)

    # After convergence, both should be near target
    level_1 = _measure_dbfs(result[1])
    level_2 = _measure_dbfs(result[2])
    # Within 4 dB of each other
    assert abs(level_1 - level_2) < 4.0, (
        f"Speakers not balanced: {level_1:.1f} vs {level_2:.1f} dBFS"
    )

    # Mix and verify no clipping
    mixed = mix_slot(result)
    assert mixed.dtype == np.int16
    mixed_level = _measure_dbfs(mixed)
    assert mixed_level < 0, f"Mixed signal at {mixed_level:.1f} dBFS — too hot"
