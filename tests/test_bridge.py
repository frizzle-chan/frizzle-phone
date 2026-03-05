import queue

import numpy as np

from frizzle_phone.audio_utils import stereo_to_mono
from frizzle_phone.bridge import (
    SILENCE_FRAME,
    PhoneAudioSource,
    mix_slot,
)


def test_stereo_to_mono_halves_length():
    stereo = b"\x00" * 3840  # 960 stereo samples
    mono = stereo_to_mono(stereo)
    assert isinstance(mono, np.ndarray)
    assert len(mono) == 960  # 960 mono samples


def test_stereo_to_mono_averages():
    """L=100, R=200 → mono=150."""
    stereo = np.array([100, 200], dtype=np.int16).tobytes()
    mono = stereo_to_mono(stereo)
    assert mono[0] == 150


def test_phone_audio_source_returns_queued_data():
    q: queue.Queue[bytes] = queue.Queue()
    frame = b"\x42" * 3840
    q.put(frame)
    source = PhoneAudioSource(q)
    assert source.read() == frame


def test_phone_audio_source_returns_silence_on_empty():
    q: queue.Queue[bytes] = queue.Queue()
    source = PhoneAudioSource(q)
    result = source.read()
    assert result == SILENCE_FRAME
    assert len(result) == 3840


def test_phone_audio_source_returns_empty_after_cleanup():
    q: queue.Queue[bytes] = queue.Queue()
    source = PhoneAudioSource(q)
    source.cleanup()
    assert source.read() == b""


def test_phone_audio_source_is_not_opus():
    q: queue.Queue[bytes] = queue.Queue()
    source = PhoneAudioSource(q)
    assert source.is_opus() is False


# ---------------------------------------------------------------------------
# Multi-speaker mixer gain reduction tests
# ---------------------------------------------------------------------------


def test_mix_slot_single_speaker():
    """Single speaker fast path returns audio unchanged."""
    samples = np.full(960, 32767, dtype=np.int16)
    np.testing.assert_array_equal(mix_slot({1: samples}), samples)


def test_mix_slot_two_speakers_moderate_no_clip():
    """Two moderate-volume speakers should not clip with gain reduction."""
    moderate = np.full(960, 20000, dtype=np.int16)
    mixed = mix_slot({1: moderate, 2: moderate})

    # 20000 * 2 / sqrt(2) ≈ 28284 — no clipping
    expected = int(20000 * 2 / np.sqrt(2))
    np.testing.assert_array_equal(mixed, expected)


def test_mix_slot_three_speakers_gain():
    """Three speakers get 1/sqrt(3) gain reduction."""
    samples = np.full(960, 16000, dtype=np.int16)
    mixed = mix_slot({1: samples, 2: samples, 3: samples})

    # 16000 * 3 / sqrt(3) ≈ 27713
    expected = int(16000 * 3 / np.sqrt(3))
    assert mixed[0] == expected


def test_mix_slot_gain_vs_hard_clip():
    """Gain reduction produces different output than hard clipping.

    Two full-volume sine waves sum to 2x amplitude. Hard clipping
    flat-tops at ±32767; 1/sqrt(2) gain preserves the waveform.
    """
    t = np.arange(960, dtype=np.float64) / 48000
    tone = (32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)

    summed = np.sum([tone, tone], axis=0, dtype=np.int32)
    hard_clipped = np.clip(summed, -32768, 32767).astype(np.int16)

    gain_reduced = mix_slot({1: tone, 2: tone})

    # Hard clipping produces flat-topped waveforms (many samples at ±32767)
    clipped_count = int(np.sum(np.abs(hard_clipped) == 32767))
    gain_clipped_count = int(np.sum(np.abs(gain_reduced) == 32767))

    assert clipped_count > 100
    assert gain_clipped_count < clipped_count
    assert not np.array_equal(hard_clipped, gain_reduced)

    rms_hard = float(np.sqrt(np.mean(hard_clipped.astype(np.float64) ** 2)))
    rms_gain = float(np.sqrt(np.mean(gain_reduced.astype(np.float64) ** 2)))
    assert rms_gain < rms_hard


def test_mix_slot_negative_no_underflow():
    """Negative full-volume samples should be gain-reduced symmetrically."""
    neg_full = np.full(960, -32768, dtype=np.int16)
    mixed = mix_slot({1: neg_full, 2: neg_full})

    # -32768 * 2 / sqrt(2) ≈ -46341 → clipped to -32768
    assert mixed[0] == -32768
