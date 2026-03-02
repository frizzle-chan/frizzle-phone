import asyncio
import queue
from unittest.mock import MagicMock, patch

import numpy as np

from frizzle_phone.bridge import (
    SILENCE_FRAME,
    PhoneAudioSink,
    PhoneAudioSource,
    stereo_to_mono,
)
from frizzle_phone.discord_patches import _patched_callback

_SSRC = 12345
_USER_ID = 42


def _make_reader(*, dave_session=None, ssrc_map=None):
    """Create a mocked AudioReader for _patched_callback tests."""
    reader = MagicMock()
    reader.error = None
    reader._last_callback_rtp = 0.0
    reader.voice_client._ssrc_to_id = ssrc_map if ssrc_map is not None else {}
    reader.voice_client._connection.dave_session = dave_session
    return reader


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


def test_phone_audio_sink_wants_opus_returns_false():
    loop = MagicMock()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
    sink = PhoneAudioSink(q, loop)
    assert sink.wants_opus() is False


def test_phone_audio_sink_uses_threadsafe_enqueue():
    """write() batches; a second write >2ms later flushes."""
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
    stereo_a = np.array([val_a, val_a] * 960, dtype=np.int16).tobytes()
    stereo_b = np.array([val_b, val_b] * 960, dtype=np.int16).tobytes()

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

        # Both speakers accumulated; mixing verified after flush below

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


# ---------------------------------------------------------------------------
# _patched_callback DAVE edge-case unit tests
# ---------------------------------------------------------------------------


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_dave_decryption(mock_rtp):
    """DAVE session decrypts transport-decrypted payload via XOR mock."""
    payload = b"\x01\x02\x03\x04" * 10
    xor_key = 0xAA
    encrypted = bytes(b ^ xor_key for b in payload)

    dave_mock = MagicMock()
    dave_mock.ready = True
    dave_mock.decrypt.side_effect = lambda uid, _mt, data: bytes(
        b ^ xor_key for b in data
    )

    reader = _make_reader(dave_session=dave_mock, ssrc_map={_SSRC: _USER_ID})
    reader.decryptor.decrypt_rtp.return_value = encrypted

    mock_packet = MagicMock()
    mock_packet.ssrc = _SSRC
    mock_packet.is_silence.return_value = False
    mock_rtp.is_rtcp.return_value = False
    mock_rtp.decode_rtp.return_value = mock_packet

    _patched_callback(reader, b"\x00" * 20)

    dave_mock.decrypt.assert_called_once()
    assert mock_packet.decrypted_data == payload
    reader.packet_router.feed_rtp.assert_called_once_with(mock_packet)


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_no_dave_session(mock_rtp):
    """Without DAVE session, transport-decrypted payload passes through directly."""
    payload = b"\xde\xad" * 20

    reader = _make_reader(dave_session=None, ssrc_map={_SSRC: _USER_ID})
    reader.decryptor.decrypt_rtp.return_value = payload

    mock_packet = MagicMock()
    mock_packet.ssrc = _SSRC
    mock_packet.is_silence.return_value = False
    mock_rtp.is_rtcp.return_value = False
    mock_rtp.decode_rtp.return_value = mock_packet

    _patched_callback(reader, b"\x00" * 20)

    assert mock_packet.decrypted_data == payload
    reader.packet_router.feed_rtp.assert_called_once_with(mock_packet)


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_dave_not_ready(mock_rtp):
    """DAVE session exists but not ready — bypass decryption."""
    payload = b"\xbe\xef" * 20

    dave_mock = MagicMock()
    dave_mock.ready = False

    reader = _make_reader(dave_session=dave_mock, ssrc_map={_SSRC: _USER_ID})
    reader.decryptor.decrypt_rtp.return_value = payload

    mock_packet = MagicMock()
    mock_packet.ssrc = _SSRC
    mock_packet.is_silence.return_value = False
    mock_rtp.is_rtcp.return_value = False
    mock_rtp.decode_rtp.return_value = mock_packet

    _patched_callback(reader, b"\x00" * 20)

    dave_mock.decrypt.assert_not_called()
    assert mock_packet.decrypted_data == payload
    reader.packet_router.feed_rtp.assert_called_once_with(mock_packet)


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_dave_unknown_ssrc(mock_rtp):
    """DAVE ready but SSRC not mapped — skip DAVE decrypt, use transport decrypted."""
    payload = b"\xca\xfe" * 20

    dave_mock = MagicMock()
    dave_mock.ready = True

    reader = _make_reader(dave_session=dave_mock, ssrc_map={})
    reader.decryptor.decrypt_rtp.return_value = payload

    mock_packet = MagicMock()
    mock_packet.ssrc = 99999  # not in map
    mock_packet.is_silence.return_value = False
    mock_rtp.is_rtcp.return_value = False
    mock_rtp.decode_rtp.return_value = mock_packet

    _patched_callback(reader, b"\x00" * 20)

    dave_mock.decrypt.assert_not_called()
    assert mock_packet.decrypted_data == payload
    reader.packet_router.feed_rtp.assert_called_once_with(mock_packet)
