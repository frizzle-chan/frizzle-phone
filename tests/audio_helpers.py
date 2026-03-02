"""Shared audio test helpers for golden-file comparisons."""

import io
import wave
from pathlib import Path

import numpy as np
import soxr

FIXTURES = Path(__file__).parent / "fixtures"

# soxr.resample is non-deterministic across process invocations (SIMD/memory
# layout causes ±1–2 LSB rounding differences on int16).  These small PCM
# differences can cross ulaw quantization boundaries, producing up to ±512
# on individual decoded samples.  We use RMSE and correlation to compare —
# soxr jitter gives RMSE ~6 and r > 0.999, while real regressions (garbled
# audio, silence, wrong rate) would blow these thresholds.
MAX_RMSE = 30.0
MIN_CORRELATION = 0.999


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV file, return (samples as int16 ndarray, sample rate)."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16)
        if wf.getnchannels() == 2:
            samples = samples.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return samples, sr


def mono_to_stereo_bytes(mono: np.ndarray) -> bytes:
    """Duplicate mono samples to interleaved stereo s16le bytes."""
    stereo = np.column_stack((mono, mono))
    return stereo.astype(np.int16).tobytes()


def pcm_to_wav(pcm: bytes, channels: int, sampwidth: int, framerate: int) -> bytes:
    """Wrap raw PCM bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_samples_check(
    obtained_path: Path,
    expected_path: Path,
    *,
    max_rmse: float = MAX_RMSE,
    min_correlation: float = MIN_CORRELATION,
) -> None:
    """Compare WAV files by RMSE and correlation, tolerating soxr jitter."""
    obtained, sr_o = read_wav(obtained_path)
    expected, sr_e = read_wav(expected_path)
    assert sr_o == sr_e, f"Sample rates differ: {sr_o} vs {sr_e}"
    assert len(obtained) == len(expected), (
        f"Sample counts differ: {len(obtained)} vs {len(expected)}"
    )
    diff = obtained.astype(np.float64) - expected.astype(np.float64)
    rmse = float(np.sqrt(np.mean(diff**2)))
    assert rmse <= max_rmse, f"RMSE {rmse:.2f} exceeds threshold {max_rmse}"
    obtained_f = obtained.astype(np.float64)
    expected_f = expected.astype(np.float64)
    corr = float(np.corrcoef(obtained_f, expected_f)[0, 1])
    assert corr >= min_correlation, (
        f"Correlation {corr:.6f} below threshold {min_correlation}"
    )


def resample_to_48k_frames(path: Path) -> list[bytes]:
    """Read a WAV, resample to 48kHz, return list of 20ms stereo frames."""
    mono, sr = read_wav(path)
    mono_48k = soxr.resample(mono, sr, 48000).astype(np.int16)
    frame_samples = 960
    frames = []
    for i in range(0, len(mono_48k), frame_samples):
        chunk = mono_48k[i : i + frame_samples]
        if len(chunk) < frame_samples:
            chunk = np.pad(chunk, (0, frame_samples - len(chunk)))
        frames.append(mono_to_stereo_bytes(chunk))
    return frames
