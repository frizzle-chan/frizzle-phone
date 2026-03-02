"""Tests for discord-ext-voice-recv monkey-patches (C2, C3)."""

import logging
from unittest.mock import MagicMock, patch

from discord.ext.voice_recv import rtp  # noqa: F401 - used in isinstance checks
from discord.ext.voice_recv.reader import AudioReader
from discord.ext.voice_recv.router import PacketRouter

from frizzle_phone.discord_patches import (
    _patched_callback,
    _patched_do_run,
    apply_discord_patches,
)

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


# ---------------------------------------------------------------------------
# C2: apply_discord_patches tests
# ---------------------------------------------------------------------------


def test_apply_discord_patches_sets_methods():
    """apply_discord_patches replaces PacketRouter._do_run and AudioReader.callback."""
    original_do_run = PacketRouter._do_run
    original_callback = AudioReader.callback
    try:
        apply_discord_patches()
        assert PacketRouter._do_run is _patched_do_run
        assert AudioReader.callback is _patched_callback
    finally:
        PacketRouter._do_run = original_do_run
        AudioReader.callback = original_callback


def test_apply_discord_patches_warns_on_version_mismatch(caplog):
    """Version mismatch triggers a warning."""
    with (
        patch("frizzle_phone.discord_patches.voice_recv") as mock_vr,
        caplog.at_level(logging.WARNING, logger="frizzle_phone.discord_patches"),
    ):
        mock_vr.__version__ = "0.6.0"
        apply_discord_patches()
    assert any("incompatible" in r.message for r in caplog.records)


def test_apply_discord_patches_no_warn_on_compatible_version(caplog):
    """Compatible version (0.5.x) does not trigger a warning."""
    with (
        patch("frizzle_phone.discord_patches.voice_recv") as mock_vr,
        caplog.at_level(logging.WARNING, logger="frizzle_phone.discord_patches"),
    ):
        mock_vr.__version__ = "0.5.3"
        apply_discord_patches()
    assert not any("incompatible" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# C2: _patched_do_run tests
# ---------------------------------------------------------------------------


def test_patched_do_run_writes_good_data():
    """Normal data flows through to sink.write()."""
    router = MagicMock()
    router._end_thread.is_set.side_effect = [False, True]
    data_mock = MagicMock()
    data_mock.source = MagicMock()
    decoder = MagicMock()
    decoder.pop_data.return_value = data_mock
    router.waiter.items = [decoder]

    _patched_do_run(router)

    router.sink.write.assert_called_once_with(data_mock.source, data_mock)


def test_patched_do_run_skips_opus_error():
    """OpusError from decoder.pop_data is caught and skipped."""
    from discord.opus import OpusError

    router = MagicMock()
    router._end_thread.is_set.side_effect = [False, True]

    bad_decoder = MagicMock()
    bad_decoder.ssrc = 1111
    opus_err = OpusError.__new__(OpusError)
    bad_decoder.pop_data.side_effect = opus_err

    good_decoder = MagicMock()
    good_data = MagicMock()
    good_data.source = MagicMock()
    good_decoder.pop_data.return_value = good_data

    router.waiter.items = [bad_decoder, good_decoder]

    _patched_do_run(router)

    # Bad decoder skipped, good decoder processed
    router.sink.write.assert_called_once_with(good_data.source, good_data)


def test_patched_do_run_skips_none_data():
    """When pop_data() returns None, sink.write() is not called."""
    router = MagicMock()
    router._end_thread.is_set.side_effect = [False, True]
    decoder = MagicMock()
    decoder.pop_data.return_value = None
    router.waiter.items = [decoder]

    _patched_do_run(router)

    router.sink.write.assert_not_called()


# ---------------------------------------------------------------------------
# C3: _patched_callback error path tests
# ---------------------------------------------------------------------------


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_crypto_error_returns(mock_rtp):
    """CryptoError during decryption returns early without feeding router."""
    from nacl.exceptions import CryptoError

    reader = _make_reader(ssrc_map={_SSRC: _USER_ID})
    mock_rtp.is_rtcp.return_value = False
    mock_packet = MagicMock()
    mock_packet.ssrc = _SSRC
    mock_rtp.decode_rtp.return_value = mock_packet
    reader.decryptor.decrypt_rtp.side_effect = CryptoError("bad")

    _patched_callback(reader, b"\x00" * 20)

    reader.packet_router.feed_rtp.assert_not_called()
    reader.packet_router.feed_rtcp.assert_not_called()


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_rtcp_feeds_router(mock_rtp):
    """RTCP packet is decoded and fed to packet_router.feed_rtcp()."""
    reader = _make_reader()

    rtcp_packet = MagicMock(spec=rtp.ReceiverReportPacket)
    mock_rtp.is_rtcp.return_value = True
    mock_rtp.decode_rtcp.return_value = rtcp_packet
    # isinstance check — ReceiverReportPacket
    mock_rtp.ReceiverReportPacket = rtp.ReceiverReportPacket

    _patched_callback(reader, b"\x00" * 20)

    reader.packet_router.feed_rtcp.assert_called_once_with(rtcp_packet)


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_rtcp_non_receiver_report_logs_debug(mock_rtp, caplog):
    """Non-ReceiverReportPacket RTCP logs debug message."""
    reader = _make_reader()

    rtcp_packet = MagicMock()
    rtcp_packet.type = 200
    # Ensure isinstance(..., ReceiverReportPacket) returns False
    rtcp_packet.__class__ = type("SenderReport", (), {})
    mock_rtp.is_rtcp.return_value = True
    mock_rtp.decode_rtcp.return_value = rtcp_packet
    mock_rtp.ReceiverReportPacket = rtp.ReceiverReportPacket

    with caplog.at_level(logging.DEBUG, logger="frizzle_phone.discord_patches"):
        _patched_callback(reader, b"\x00" * 20)

    assert any("Unexpected RTCP packet" in r.message for r in caplog.records)


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_ip_discovery_returns(mock_rtp, caplog):
    """74-byte IP discovery packet returns without 'Error unpacking' log."""
    reader = _make_reader()

    mock_rtp.is_rtcp.side_effect = Exception("trigger except branch")

    # Build a 74-byte packet with byte[1] == 0x02
    packet_data = bytearray(74)
    packet_data[1] = 0x02

    with caplog.at_level(logging.DEBUG, logger="frizzle_phone.discord_patches"):
        _patched_callback(reader, bytes(packet_data))

    # Should not log "Error unpacking packet"
    assert not any("Error unpacking" in r.message for r in caplog.records)
    # Should log debug about IP discovery
    assert any("IP discovery" in r.message for r in caplog.records)
    reader.packet_router.feed_rtp.assert_not_called()


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_unknown_exception_logs_error(mock_rtp, caplog):
    """Non-IP-discovery exception logs 'Error unpacking packet'."""
    reader = _make_reader()

    mock_rtp.is_rtcp.side_effect = Exception("something else")

    with caplog.at_level(logging.ERROR, logger="frizzle_phone.discord_patches"):
        _patched_callback(reader, b"\x00" * 20)

    assert any("Error unpacking" in r.message for r in caplog.records)


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_silence_filtered_for_unknown_ssrc(mock_rtp):
    """Silence from an unknown SSRC is filtered (not fed to router)."""
    reader = _make_reader(ssrc_map={})

    mock_packet = MagicMock()
    mock_packet.ssrc = 99999  # not in map
    mock_packet.is_silence.return_value = True
    mock_rtp.is_rtcp.return_value = False
    mock_rtp.decode_rtp.return_value = mock_packet
    reader.decryptor.decrypt_rtp.return_value = b"\x00" * 20

    _patched_callback(reader, b"\x00" * 20)

    reader.packet_router.feed_rtp.assert_not_called()


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_self_error_stops(mock_rtp):
    """When reader.error is set, reader.stop() is called."""
    reader = _make_reader(ssrc_map={_SSRC: _USER_ID})
    reader.error = RuntimeError("previous error")

    mock_packet = MagicMock()
    mock_packet.ssrc = _SSRC
    mock_packet.is_silence.return_value = False
    mock_rtp.is_rtcp.return_value = False
    mock_rtp.decode_rtp.return_value = mock_packet
    reader.decryptor.decrypt_rtp.return_value = b"\x00" * 20

    _patched_callback(reader, b"\x00" * 20)

    reader.stop.assert_called_once()
    reader.packet_router.feed_rtp.assert_not_called()


@patch("frizzle_phone.discord_patches.rtp")
def test_patched_callback_feed_rtp_exception_sets_error(mock_rtp):
    """Exception in feed_rtp sets self.error and calls stop()."""
    reader = _make_reader(ssrc_map={_SSRC: _USER_ID})

    mock_packet = MagicMock()
    mock_packet.ssrc = _SSRC
    mock_packet.is_silence.return_value = False
    mock_rtp.is_rtcp.return_value = False
    mock_rtp.decode_rtp.return_value = mock_packet
    reader.decryptor.decrypt_rtp.return_value = b"\x00" * 20

    err = ValueError("decode failed")
    reader.packet_router.feed_rtp.side_effect = err

    _patched_callback(reader, b"\x00" * 20)

    assert reader.error is err
    reader.stop.assert_called_once()
