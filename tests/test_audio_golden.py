"""Golden-file test for Discord→Phone audio pipeline."""

from unittest.mock import MagicMock

import numpy as np

from frizzle_phone.agc import AgcBank
from frizzle_phone.bridge import PhoneAudioSink, _new_resampler, mix_slot
from frizzle_phone.rtp.pcmu import pcm16_arr_to_ulaw, ulaw_to_pcm
from tests.audio_helpers import (
    FIXTURES,
    pcm_to_wav,
    resample_to_48k_frames,
    wav_samples_check,
)


def _run_sink(
    speaker_frames: dict[int, list[bytes]],
    *,
    agc_bank: AgcBank | None = None,
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
        if agc_bank is not None:
            slot = agc_bank.process_slot(slot)
        mixed = mix_slot(slot)

        for chunk_8k in resampler.feed(mixed):
            ulaw_payloads.append(pcm16_arr_to_ulaw(chunk_8k))

    ulaw_bytes = b"".join(ulaw_payloads)
    pcm_8k = ulaw_to_pcm(ulaw_bytes)
    return pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)


def _scale_frames(frames: list[bytes], target_dbfs: float) -> list[bytes]:
    """Scale stereo frame bytes to a target dBFS level.

    Computes a single gain from the global RMS across all frames so that
    natural speech dynamics are preserved (no per-frame normalization).
    """
    # Measure global RMS across all frames (left channel = mono source)
    all_mono = np.concatenate(
        [
            np.frombuffer(f, dtype=np.int16).reshape(-1, 2)[:, 0].astype(np.float64)
            for f in frames
        ]
    )
    rms = np.sqrt(np.mean(all_mono**2))
    if rms < 1.0:
        return list(frames)
    current_dbfs = 20.0 * np.log10(rms / 32768.0)
    gain = 10.0 ** ((target_dbfs - current_dbfs) / 20.0)

    scaled = []
    for frame_bytes in frames:
        stereo = np.frombuffer(frame_bytes, dtype=np.int16)
        stereo_scaled = np.clip(stereo.astype(np.float64) * gain, -32768, 32767).astype(
            np.int16
        )
        scaled.append(stereo_scaled.tobytes())
    return scaled


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


def test_agc_mixed_loudness(file_regression):
    """AGC normalizes varied loudness levels through the full pipeline."""
    frames_1 = resample_to_48k_frames(FIXTURES / "speech_sample.wav")
    frames_2 = resample_to_48k_frames(FIXTURES / "speech_sample_2.wav")

    # Scale to different levels.  The speech samples peak near 0 dBFS
    # (crest factor ~13.5 dB), so targets above ~-14 dBFS RMS clip peaks
    # and introduce hard-clipping distortion.  Keep all targets below that.
    very_quiet = _scale_frames(frames_1, -35.0)
    quiet = _scale_frames(frames_2, -28.0)
    normal = _scale_frames(frames_1, -20.0)
    loud = _scale_frames(frames_2, -15.0)

    # Build segments: each ~1s, overlapping combinations
    seg_len = 50  # 50 frames = 1s

    # Segment 1: quiet speaker 1 + loud speaker 2
    seg1: dict[int, list[bytes]] = {
        1: very_quiet[:seg_len],
        2: loud[:seg_len],
    }
    # Segment 2: two quiet speakers
    seg2: dict[int, list[bytes]] = {
        3: quiet[:seg_len],
        4: very_quiet[:seg_len],
    }
    # Segment 3: two loud speakers
    seg3: dict[int, list[bytes]] = {
        5: loud[:seg_len],
        6: normal[:seg_len],
    }
    # Segment 4: three speakers at mixed levels
    seg4: dict[int, list[bytes]] = {
        7: very_quiet[:seg_len],
        8: normal[:seg_len],
        9: loud[:seg_len],
    }

    # Stitch segments sequentially through a single AGC bank
    agc_bank = AgcBank()
    all_wav_parts = []
    for segment in [seg1, seg2, seg3, seg4]:
        wav_bytes = _run_sink(segment, agc_bank=agc_bank)
        all_wav_parts.append(wav_bytes)

    # Concatenate the raw PCM from each WAV segment
    pcm_parts = []
    for wav_bytes in all_wav_parts:
        samples, sr = read_wav_bytes(wav_bytes)
        pcm_parts.append(samples)
    combined_pcm = np.concatenate(pcm_parts)
    combined_wav = pcm_to_wav(
        combined_pcm.tobytes(), channels=1, sampwidth=2, framerate=8000
    )

    file_regression.check(
        combined_wav, binary=True, extension=".wav", check_fn=wav_samples_check
    )


def read_wav_bytes(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Read WAV from bytes, return (int16 samples, sample rate)."""
    import io
    import wave

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16)
    return samples, sr
