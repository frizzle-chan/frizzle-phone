import queue
import struct

from frizzle_phone.bridge import (
    SILENCE_FRAME,
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
