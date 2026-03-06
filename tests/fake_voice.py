"""Fake voice client for E2E testing without Discord."""

from __future__ import annotations

import numpy as np

# C major chord frequencies (Hz)
CHORD_FREQS = [261.63, 329.63, 392.00, 523.25, 659.25]
SAMPLE_RATE = 48000
FRAME_SAMPLES = 960  # 20ms at 48kHz
AMPLITUDE = 6000


def sine_tone_speakers(
    n_ticks: int,
    *,
    freqs: list[float] | None = None,
    amplitude: int = AMPLITUDE,
) -> dict[int, list[np.ndarray]]:
    """Generate per-speaker sine tone frames for FakeVoiceRecvClient.

    Returns {user_id: [frame0, frame1, ...]} where each frame is 960 mono int16 samples.
    """
    freqs = freqs or CHORD_FREQS
    speakers: dict[int, list[np.ndarray]] = {}
    for i, freq in enumerate(freqs):
        user_id = i + 1
        total_samples = n_ticks * FRAME_SAMPLES
        t = np.arange(total_samples, dtype=np.float64) / SAMPLE_RATE
        wave = (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.int16)
        frames = [
            wave[j * FRAME_SAMPLES : (j + 1) * FRAME_SAMPLES] for j in range(n_ticks)
        ]
        speakers[user_id] = frames
    return speakers


class FakeVoiceRecvClient:
    """Test double for VoiceRecvClient that produces deterministic audio."""

    def __init__(self, speakers: dict[int, list[np.ndarray]]) -> None:
        self._speakers = speakers
        self._tick_idx = 0
        self._listening = False
        self._max_ticks = max((len(f) for f in speakers.values()), default=0)

    def play(self, source: object) -> None:
        pass  # Phone→Discord direction not under test

    def start_listening(self) -> None:
        self._listening = True

    def stop_listening(self) -> None:
        self._listening = False

    def stop(self) -> None:
        self._listening = False

    def pop_tick(self) -> dict[int, np.ndarray]:
        if not self._listening or self._tick_idx >= self._max_ticks:
            return {}
        tick: dict[int, np.ndarray] = {}
        for user_id, frames in self._speakers.items():
            if self._tick_idx < len(frames):
                tick[user_id] = frames[self._tick_idx]
        self._tick_idx += 1
        return tick

    def is_connected(self) -> bool:
        return True

    async def disconnect(self, *, force: bool = False) -> None:
        pass
