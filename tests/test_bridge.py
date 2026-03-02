import asyncio
import queue
import struct
from unittest.mock import MagicMock, patch

import numpy as np

from frizzle_phone.bridge import (
    SILENCE_FRAME,
    PhoneAudioSink,
    PhoneAudioSource,
    stereo_to_mono,
)


def test_stereo_to_mono_halves_length():
    stereo = b"\x00" * 3840  # 960 stereo samples
    mono = stereo_to_mono(stereo)
    assert len(mono) == 1920  # 960 mono samples


def test_stereo_to_mono_averages():
    """L=100, R=200 → mono=150."""
    stereo = struct.pack("<hh", 100, 200)
    mono = stereo_to_mono(stereo)
    sample = struct.unpack("<h", mono)[0]
    assert sample == 150


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


def test_phone_audio_source_returns_empty_when_stopped():
    q: queue.Queue[bytes] = queue.Queue()
    source = PhoneAudioSource(q)
    source.stop()
    assert source.read() == b""


def test_phone_audio_source_is_not_opus():
    q: queue.Queue[bytes] = queue.Queue()
    source = PhoneAudioSource(q)
    assert source.is_opus() is False


def test_phone_audio_sink_uses_threadsafe_enqueue():
    """write() batches; a second write >15ms later flushes."""
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
    sink = PhoneAudioSink(q, loop)

    user_a = MagicMock()
    user_a.id = 1
    data = MagicMock()
    data.pcm = b"\x00" * 3840  # 20ms 48kHz stereo silence

    t = 1000.0
    with patch("frizzle_phone.bridge.time") as mock_time:
        mock_time.monotonic.return_value = t
        sink.write(user_a, data)
        # First write accumulates — no flush yet
        loop.call_soon_threadsafe.assert_not_called()

        user_b = MagicMock()
        user_b.id = 2
        mock_time.monotonic.return_value = t + 0.020
        sink.write(user_b, data)
        # Second write flushes the previous batch
        loop.call_soon_threadsafe.assert_called_once()


def test_phone_audio_sink_mixes_multiple_speakers():
    """Two speakers' PCM should be summed in the flushed output."""
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
    sink = PhoneAudioSink(q, loop)

    # 960 stereo samples = 3840 bytes (20ms @ 48kHz)
    val_a = 1000
    val_b = 2000
    stereo_a = struct.pack("<hh", val_a, val_a) * 960
    stereo_b = struct.pack("<hh", val_b, val_b) * 960

    user_a = MagicMock()
    user_a.id = 1
    user_b = MagicMock()
    user_b.id = 2
    data_a = MagicMock()
    data_a.pcm = stereo_a
    data_b = MagicMock()
    data_b.pcm = stereo_b

    t = 1000.0
    with patch("frizzle_phone.bridge.time") as mock_time:
        # Both writes in the same batch
        mock_time.monotonic.return_value = t
        sink.write(user_a, data_a)
        sink.write(user_b, data_b)

        # Verify pending frames contain both users' mono PCM
        assert 1 in sink._pending_frames
        assert 2 in sink._pending_frames
        mono_a = sink._pending_frames[1]
        mono_b = sink._pending_frames[2]
        assert np.all(mono_a == val_a)
        assert np.all(mono_b == val_b)

        # Trigger flush via next-batch write
        mock_time.monotonic.return_value = t + 0.020
        dummy_user = MagicMock()
        dummy_user.id = 99
        dummy_data = MagicMock()
        dummy_data.pcm = b"\x00" * 3840
        sink.write(dummy_user, dummy_data)

    # The flush should have produced a call_soon_threadsafe call
    loop.call_soon_threadsafe.assert_called_once()
    enqueued_ulaw = loop.call_soon_threadsafe.call_args[0][1]
    # 960 mono samples @ 48kHz → 160 samples @ 8kHz = 160 bytes ulaw
    assert len(enqueued_ulaw) == 160
    assert enqueued_ulaw != b"\xff" * 160  # not silence
