"""Golden-file test for Discord→Phone audio pipeline."""

from unittest.mock import MagicMock

import numpy as np

from frizzle_phone.bridge import PhoneAudioSink, _new_resampler
from frizzle_phone.rtp.pcmu import pcm16_arr_to_ulaw, ulaw_to_pcm
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
    Speakers are interleaved within each 20ms batch, then drained and
    mixed (replicating rtp_send_loop's per-tick logic).
    """
    sink = PhoneAudioSink()
    resampler = _new_resampler()

    users = {}
    for uid in speaker_frames:
        u = MagicMock()
        u.id = uid
        users[uid] = u

    max_frames = max(len(f) for f in speaker_frames.values())
    ulaw_payloads = []

    for i in range(max_frames):
        for uid, frames in speaker_frames.items():
            if i < len(frames):
                data = MagicMock()
                data.pcm = frames[i]
                sink.write(users[uid], data)

        raw_frames = sink.drain()
        if not raw_frames:
            continue

        # Slot grouping (same algorithm as rtp_send_loop)
        slots: list[dict[int, np.ndarray]] = []
        current_slot: dict[int, np.ndarray] = {}
        for user_key, mono in raw_frames:
            if user_key in current_slot:
                slots.append(current_slot)
                current_slot = {}
            current_slot[user_key] = mono
        if current_slot:
            slots.append(current_slot)

        slot = slots[-1]
        if len(slot) == 1:
            mixed = next(iter(slot.values()))
        else:
            mixed = np.clip(
                np.sum(list(slot.values()), axis=0, dtype=np.int32),
                -32768,
                32767,
            ).astype(np.int16)

        arr_8k = resampler.resample_chunk(mixed)
        ulaw_payloads.append(pcm16_arr_to_ulaw(arr_8k))

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
