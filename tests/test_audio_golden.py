"""Golden-file test for Discord→Phone audio pipeline."""

import asyncio
import io
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import soxr

from frizzle_phone.bridge import PhoneAudioSink
from frizzle_phone.rtp.pcmu import ulaw_to_pcm

FIXTURES = Path(__file__).parent / "fixtures"

# soxr.resample is non-deterministic across process invocations (SIMD/memory
# layout causes ±1–2 LSB rounding differences on int16).  These small PCM
# differences can cross ulaw quantization boundaries, producing up to ±512
# on individual decoded samples.  We use RMSE and correlation to compare —
# soxr jitter gives RMSE ~6 and r > 0.999, while real regressions (garbled
# audio, silence, wrong rate) would blow these thresholds.
_MAX_RMSE = 30.0
_MIN_CORRELATION = 0.999


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV file, return (samples as int16 ndarray, sample rate)."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16)
        if wf.getnchannels() == 2:
            samples = samples.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return samples, sr


def _mono_to_stereo_bytes(mono: np.ndarray) -> bytes:
    """Duplicate mono samples to interleaved stereo s16le bytes."""
    stereo = np.column_stack((mono, mono))
    return stereo.astype(np.int16).tobytes()


def _pcm_to_wav(pcm: bytes, channels: int, sampwidth: int, framerate: int) -> bytes:
    """Wrap raw PCM bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _wav_samples_check(obtained_path: Path, expected_path: Path) -> None:
    """Compare WAV files by RMSE and correlation, tolerating soxr jitter."""
    obtained, sr_o = _read_wav(obtained_path)
    expected, sr_e = _read_wav(expected_path)
    assert sr_o == sr_e, f"Sample rates differ: {sr_o} vs {sr_e}"
    assert len(obtained) == len(expected), (
        f"Sample counts differ: {len(obtained)} vs {len(expected)}"
    )
    diff = obtained.astype(np.float64) - expected.astype(np.float64)
    rmse = float(np.sqrt(np.mean(diff**2)))
    assert rmse <= _MAX_RMSE, f"RMSE {rmse:.2f} exceeds threshold {_MAX_RMSE}"
    obtained_f = obtained.astype(np.float64)
    expected_f = expected.astype(np.float64)
    corr = float(np.corrcoef(obtained_f, expected_f)[0, 1])
    assert corr >= _MIN_CORRELATION, (
        f"Correlation {corr:.6f} below threshold {_MIN_CORRELATION}"
    )


def test_discord_to_phone_pipeline(file_regression):
    """Feed speech WAV through Discord→Phone pipeline, regression-check output."""
    # Read input WAV and resample to 48kHz (Discord's native rate)
    mono, sr = _read_wav(FIXTURES / "speech_sample.wav")
    mono_48k = soxr.resample(mono, sr, 48000).astype(np.int16)

    # Chunk into 20ms Discord frames (960 samples → 3840 bytes stereo)
    frame_samples = 960
    frames = []
    for i in range(0, len(mono_48k), frame_samples):
        chunk = mono_48k[i : i + frame_samples]
        if len(chunk) < frame_samples:
            chunk = np.pad(chunk, (0, frame_samples - len(chunk)))
        frames.append(_mono_to_stereo_bytes(chunk))

    # Set up PhoneAudioSink with mocked event loop
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
    sink = PhoneAudioSink(q, loop)

    user = MagicMock()
    user.id = 1

    # Feed frames, advancing time by 20ms each to trigger batch flushes
    t = 1000.0
    with patch("frizzle_phone.bridge.time") as mock_time:
        for i, frame in enumerate(frames):
            mock_time.monotonic.return_value = t + i * 0.020
            data = MagicMock()
            data.pcm = frame
            sink.write(user, data)
        sink.cleanup()

    # Collect all enqueued ulaw payloads
    ulaw_payloads = []
    for call in loop.call_soon_threadsafe.call_args_list:
        ulaw_payloads.append(call[0][1])
    ulaw_bytes = b"".join(ulaw_payloads)

    # Decode ulaw → PCM and wrap as 8kHz mono WAV
    pcm_8k = ulaw_to_pcm(ulaw_bytes)
    wav_bytes = _pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)

    file_regression.check(
        wav_bytes, binary=True, extension=".wav", check_fn=_wav_samples_check
    )
