"""Golden-file test for Discord→Phone audio pipeline."""

import asyncio
from unittest.mock import MagicMock, patch

from frizzle_phone.bridge import PhoneAudioSink
from frizzle_phone.rtp.pcmu import ulaw_to_pcm
from tests.audio_helpers import (
    FIXTURES,
    pcm_to_wav,
    resample_to_48k_frames,
    wav_samples_check,
)


def _run_sink(
    speaker_frames: dict[int, list[bytes]],
) -> bytes:
    """Feed speaker frames through PhoneAudioSink, return output WAV bytes.

    speaker_frames maps user_id → list of 20ms stereo frame bytes.
    Speakers are interleaved within each 20ms batch window.
    """
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
    sink = PhoneAudioSink(q, loop)

    users = {}
    for uid in speaker_frames:
        u = MagicMock()
        u.id = uid
        users[uid] = u

    max_frames = max(len(f) for f in speaker_frames.values())
    t = 1000.0

    with patch("frizzle_phone.bridge.time") as mock_time:
        for i in range(max_frames):
            mock_time.monotonic.return_value = t + i * 0.020
            for uid, frames in speaker_frames.items():
                if i < len(frames):
                    data = MagicMock()
                    data.pcm = frames[i]
                    sink.write(users[uid], data)
        sink.cleanup()

    ulaw_payloads = []
    for call in loop.call_soon_threadsafe.call_args_list:
        ulaw_payloads.append(call[0][1])
    ulaw_bytes = b"".join(ulaw_payloads)

    pcm_8k = ulaw_to_pcm(ulaw_bytes)
    return pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)


def test_discord_to_phone_pipeline(file_regression):
    """Feed speech WAV through Discord→Phone pipeline, regression-check output."""
    frames = resample_to_48k_frames(FIXTURES / "speech_sample.wav")
    wav_bytes = _run_sink({1: frames})
    file_regression.check(
        wav_bytes, binary=True, extension=".wav", check_fn=wav_samples_check
    )


def test_discord_to_phone_two_speakers(file_regression):
    """Mix two speakers through the pipeline, regression-check output."""
    frames_a = resample_to_48k_frames(FIXTURES / "speech_sample.wav")
    frames_b = resample_to_48k_frames(FIXTURES / "speech_sample_2.wav")
    wav_bytes = _run_sink({1: frames_a, 2: frames_b})
    file_regression.check(
        wav_bytes, binary=True, extension=".wav", check_fn=wav_samples_check
    )
