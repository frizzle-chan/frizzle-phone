"""Tests for the FakeVoiceRecvClient test helper."""

from __future__ import annotations

import numpy as np
import pytest

from tests.fake_voice import FakeVoiceRecvClient, sine_tone_speakers


def test_sine_tone_speakers_returns_5_speakers() -> None:
    speakers = sine_tone_speakers(n_ticks=10)
    assert len(speakers) == 5
    for _user_id, frames in speakers.items():
        assert len(frames) == 10
        assert frames[0].shape == (960,)
        assert frames[0].dtype == np.int16


def test_pop_tick_returns_all_speakers() -> None:
    speakers = sine_tone_speakers(n_ticks=3)
    fake = FakeVoiceRecvClient(speakers)
    fake.start_listening()

    tick = fake.pop_tick()
    assert len(tick) == 5
    for _user_id, frame in tick.items():
        assert frame.shape == (960,)


def test_pop_tick_exhausts_frames() -> None:
    speakers = sine_tone_speakers(n_ticks=2)
    fake = FakeVoiceRecvClient(speakers)
    fake.start_listening()

    tick1 = fake.pop_tick()
    assert len(tick1) == 5
    tick2 = fake.pop_tick()
    assert len(tick2) == 5
    tick3 = fake.pop_tick()
    assert tick3 == {}


def test_pop_tick_before_start_listening_returns_empty() -> None:
    speakers = sine_tone_speakers(n_ticks=3)
    fake = FakeVoiceRecvClient(speakers)
    assert fake.pop_tick() == {}


def test_play_accepts_source() -> None:
    fake = FakeVoiceRecvClient({})
    fake.play(object())  # should not raise


def test_is_connected_returns_true() -> None:
    fake = FakeVoiceRecvClient({})
    assert fake.is_connected() is True


@pytest.mark.asyncio
async def test_disconnect_is_noop() -> None:
    fake = FakeVoiceRecvClient({})
    await fake.disconnect()  # should not raise
