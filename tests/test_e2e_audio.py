"""End-to-end audio pipeline test: DAVE decrypt → opus decode → sink → RTP → UDP."""

import asyncio
from functools import partial
from unittest.mock import MagicMock, patch

import discord.opus
import numpy as np
import pytest

from frizzle_phone.bridge import PhoneAudioSink, rtp_send_loop, stereo_to_mono
from frizzle_phone.discord_patches import _patched_callback
from frizzle_phone.rtp.pcmu import ulaw_to_pcm
from tests.audio_helpers import (
    FIXTURES,
    pcm_to_wav,
    resample_to_48k_frames,
    wav_samples_check,
)


def _xor_bytes(data: bytes, key: int = 0xAA) -> bytes:
    """Single-byte XOR transform."""
    return bytes(b ^ key for b in data)


def _parse_rtp_payload(data: bytes) -> bytes:
    """Extract payload from an RTP packet (skip fixed 12-byte header)."""
    return data[12:] if len(data) > 12 else b""


class _RtpCollector(asyncio.DatagramProtocol):
    """Receives UDP datagrams into a list."""

    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.packets.append(data)


class _PacedSink(PhoneAudioSink):
    """Sink that releases pre-computed frames one per drain() call.

    Used in e2e tests to provide deterministic pacing without
    depending on async timing between feeder and send loop.
    """

    def __init__(self, frames: list[tuple[int, np.ndarray]]) -> None:
        super().__init__()
        self._test_frames = frames
        self._test_idx = 0

    def drain(self) -> list[tuple[int, np.ndarray]]:
        if self._test_idx < len(self._test_frames):
            frame = self._test_frames[self._test_idx]
            self._test_idx += 1
            return [frame]
        return []


@pytest.mark.asyncio
async def test_dave_to_rtp_e2e(file_regression):
    """Full E2E: DAVE decrypt → opus decode → sink → RTP/UDP → golden file."""
    # Step 1 — Prepare audio frames
    frames = resample_to_48k_frames(FIXTURES / "speech_sample.wav")

    # Step 2 — Opus encode + XOR encrypt
    encoder = discord.opus.Encoder()
    xor_key = 0xAA
    opus_packets = []
    for pcm_frame in frames:
        opus_data = encoder.encode(pcm_frame, 960)
        encrypted = _xor_bytes(opus_data, xor_key)
        opus_packets.append((opus_data, encrypted))

    # Step 3 — Feed through _patched_callback with mocked reader
    ssrc, user_id = 12345, 42

    reader = MagicMock()
    reader.error = None
    reader._last_callback_rtp = 0.0
    reader.voice_client._ssrc_to_id = {ssrc: user_id}

    dave_mock = MagicMock()
    dave_mock.ready = True
    dave_mock.decrypt.side_effect = lambda uid, _mt, data: _xor_bytes(data, xor_key)
    reader.voice_client._connection.dave_session = dave_mock

    mock_packet = MagicMock()
    mock_packet.ssrc = ssrc
    mock_packet.is_silence.return_value = False

    decoded_pcm_frames = []
    decoder = discord.opus.Decoder()

    with patch("frizzle_phone.discord_patches.rtp") as mock_rtp:
        mock_rtp.is_rtcp.return_value = False
        mock_rtp.decode_rtp.return_value = mock_packet

        for original_opus, encrypted_opus in opus_packets:
            reader.decryptor.decrypt_rtp.return_value = encrypted_opus

            _patched_callback(reader, b"\x00" * 20)

            assert mock_packet.decrypted_data == original_opus
            pcm = decoder.decode(mock_packet.decrypted_data, fec=False)
            decoded_pcm_frames.append(pcm)

    # Step 4 — Convert decoded stereo PCM to (user_key, mono) tuples
    mono_frames = [(user_id, stereo_to_mono(pcm)) for pcm in decoded_pcm_frames]

    # Step 5 — Send over real UDP via rtp_send_loop with paced sink
    sink = _PacedSink(mono_frames)

    loop = asyncio.get_running_loop()
    collector = _RtpCollector()
    recv_transport, _ = await loop.create_datagram_endpoint(
        lambda: collector, local_addr=("127.0.0.1", 0)
    )
    recv_port = recv_transport.get_extra_info("sockname")[1]
    send_transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("127.0.0.1", recv_port)
    )

    stop_event = asyncio.Event()
    expected_count = len(mono_frames)

    task = asyncio.create_task(
        rtp_send_loop(
            sink,
            send_transport,
            ("127.0.0.1", recv_port),
            stop_event=stop_event,
        )
    )

    # Wait for all data packets (real 20ms pacing ≈ 6s for ~290 frames)
    for _ in range(1500):
        await asyncio.sleep(0.01)
        if len(collector.packets) >= expected_count:
            break

    stop_event.set()
    await task

    send_transport.close()
    recv_transport.close()

    assert len(collector.packets) >= expected_count, (
        f"Only received {len(collector.packets)}/{expected_count} packets"
    )

    # Step 6 — Collect, decode, compare
    received_ulaw = b""
    for pkt in collector.packets[:expected_count]:
        received_ulaw += _parse_rtp_payload(pkt)

    pcm_8k = ulaw_to_pcm(received_ulaw)
    wav_bytes = pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)

    # Opus encode/decode amplifies soxr platform jitter — use wider thresholds
    # than the direct-PCM golden tests (observed cross-platform RMSE ~108).
    check_fn = partial(wav_samples_check, max_rmse=250.0, min_correlation=0.98)
    file_regression.check(wav_bytes, binary=True, extension=".wav", check_fn=check_fn)
