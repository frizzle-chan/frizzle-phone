import queue
from unittest.mock import MagicMock, patch

import numpy as np

from frizzle_phone.bridge import (
    SILENCE_FRAME,
    PhoneAudioSink,
    PhoneAudioSource,
    mix_slot,
    stereo_to_mono,
)
from frizzle_phone.bridge_stats import BridgeStats
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
    sink = PhoneAudioSink()
    assert sink.wants_opus() is False


def test_phone_audio_sink_drain_returns_accumulated_frames():
    """write() accumulates frames; drain() returns and clears them."""
    sink = PhoneAudioSink()

    user_a = MagicMock()
    user_a.id = 1
    user_b = MagicMock()
    user_b.id = 2
    data = MagicMock()
    data.pcm = b"\x00" * 3840  # 20ms 48kHz stereo silence

    sink.write(user_a, data)
    sink.write(user_b, data)

    frames = sink.drain()
    assert len(frames) == 2
    assert frames[0][0] == 1  # user_key
    assert frames[1][0] == 2

    # Buffer is now empty
    assert sink.drain() == []


def test_phone_audio_sink_drain_five_speakers_one_slot():
    """5 speakers in one epoch → slot grouping yields 1 slot."""
    from frizzle_phone.bridge import _new_resampler
    from frizzle_phone.rtp.pcmu import pcm16_arr_to_ulaw

    sink = PhoneAudioSink()

    for uid in range(1, 6):
        user = MagicMock()
        user.id = uid
        data = MagicMock()
        val = uid * 1000
        data.pcm = np.array([val, val] * 960, dtype=np.int16).tobytes()
        sink.write(user, data)

    frames = sink.drain()
    assert len(frames) == 5

    # Slot grouping (same algorithm as rtp_send_loop)
    slots: list[dict[int, np.ndarray]] = []
    current_slot: dict[int, np.ndarray] = {}
    for user_key, mono in frames:
        if user_key in current_slot:
            slots.append(current_slot)
            current_slot = {}
        current_slot[user_key] = mono
    if current_slot:
        slots.append(current_slot)

    assert len(slots) == 1  # all 5 speakers in one slot
    assert len(slots[0]) == 5

    # Mix with gain reduction produces valid ulaw
    mixed = mix_slot(slots[0])
    resampler = _new_resampler()
    # Feed multiple frames to prime the sinc filter — LQ needs a few
    # input chunks before producing output.
    all_ulaw = b""
    for _ in range(5):
        for chunk_8k in resampler.feed(mixed):
            all_ulaw += pcm16_arr_to_ulaw(chunk_8k)
    assert len(all_ulaw) > 0, "Resampler should produce output after multiple feeds"
    assert len(all_ulaw) % 160 == 0, "Output should be multiples of 160 bytes"
    assert all_ulaw[:160] != b"\xff" * 160  # not silence


def test_phone_audio_sink_burst_creates_multiple_slots():
    """Burst delivery (multiple epochs) → slot grouping creates multiple slots."""
    sink = PhoneAudioSink(stats=BridgeStats())

    # Simulate 3 epochs of user 1 speaking: user_key repeats → multiple slots
    user = MagicMock()
    user.id = 1
    for _ in range(3):
        data = MagicMock()
        data.pcm = np.array([500, 500] * 960, dtype=np.int16).tobytes()
        sink.write(user, data)

    frames = sink.drain()
    assert len(frames) == 3

    # Slot grouping
    slots: list[dict[int, np.ndarray]] = []
    current_slot: dict[int, np.ndarray] = {}
    for user_key, mono in frames:
        if user_key in current_slot:
            slots.append(current_slot)
            current_slot = {}
        current_slot[user_key] = mono
    if current_slot:
        slots.append(current_slot)

    assert len(slots) == 3  # 3 separate slots (key repeats)
    assert all(1 in s for s in slots)


def test_phone_audio_sink_cleanup_drains():
    """cleanup() discards remaining frames."""
    sink = PhoneAudioSink()
    user = MagicMock()
    user.id = 1
    data = MagicMock()
    data.pcm = b"\x00" * 3840
    sink.write(user, data)
    sink.cleanup()
    assert sink.drain() == []


# ---------------------------------------------------------------------------
# _patched_callback DAVE edge-case unit tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Multi-speaker mixer gain reduction tests
# ---------------------------------------------------------------------------


def test_mix_slot_single_speaker():
    """Single speaker fast path returns audio unchanged."""
    samples = np.full(960, 32767, dtype=np.int16)
    np.testing.assert_array_equal(mix_slot({1: samples}), samples)


def test_mix_slot_two_speakers_moderate_no_clip():
    """Two moderate-volume speakers should not clip with gain reduction."""
    moderate = np.full(960, 20000, dtype=np.int16)
    mixed = mix_slot({1: moderate, 2: moderate})

    # 20000 * 2 / sqrt(2) ≈ 28284 — no clipping
    expected = int(20000 * 2 / np.sqrt(2))
    np.testing.assert_array_equal(mixed, expected)


def test_mix_slot_three_speakers_gain():
    """Three speakers get 1/sqrt(3) gain reduction."""
    samples = np.full(960, 16000, dtype=np.int16)
    mixed = mix_slot({1: samples, 2: samples, 3: samples})

    # 16000 * 3 / sqrt(3) ≈ 27713
    expected = int(16000 * 3 / np.sqrt(3))
    assert mixed[0] == expected


def test_mix_slot_gain_vs_hard_clip():
    """Gain reduction produces different output than hard clipping.

    Two full-volume sine waves sum to 2x amplitude. Hard clipping
    flat-tops at ±32767; 1/sqrt(2) gain preserves the waveform.
    """
    t = np.arange(960, dtype=np.float64) / 48000
    tone = (32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)

    summed = np.sum([tone, tone], axis=0, dtype=np.int32)
    hard_clipped = np.clip(summed, -32768, 32767).astype(np.int16)

    gain_reduced = mix_slot({1: tone, 2: tone})

    # Hard clipping produces flat-topped waveforms (many samples at ±32767)
    clipped_count = int(np.sum(np.abs(hard_clipped) == 32767))
    gain_clipped_count = int(np.sum(np.abs(gain_reduced) == 32767))

    assert clipped_count > 100
    assert gain_clipped_count < clipped_count
    assert not np.array_equal(hard_clipped, gain_reduced)

    rms_hard = float(np.sqrt(np.mean(hard_clipped.astype(np.float64) ** 2)))
    rms_gain = float(np.sqrt(np.mean(gain_reduced.astype(np.float64) ** 2)))
    assert rms_gain < rms_hard


def test_mix_slot_negative_no_underflow():
    """Negative full-volume samples should be gain-reduced symmetrically."""
    neg_full = np.full(960, -32768, dtype=np.int16)
    mixed = mix_slot({1: neg_full, 2: neg_full})

    # -32768 * 2 / sqrt(2) ≈ -46341 → clipped to -32768
    assert mixed[0] == -32768


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
