"""End-to-end audio pipeline test: opus encode → decode → pop_tick → RTP → UDP."""

import asyncio
from functools import partial

import discord.opus
import numpy as np
import pytest

from frizzle_phone.audio_utils import stereo_to_mono
from frizzle_phone.bridge import rtp_send_loop
from frizzle_phone.rtp.pcmu import ulaw_to_pcm
from tests.audio_helpers import (
    FIXTURES,
    pcm_to_wav,
    resample_to_48k_frames,
    wav_samples_check,
)


def _parse_rtp_payload(data: bytes) -> bytes:
    """Extract payload from an RTP packet (skip fixed 12-byte header)."""
    return data[12:] if len(data) > 12 else b""


class _RtpCollector(asyncio.DatagramProtocol):
    """Receives UDP datagrams into a list."""

    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.packets.append(data)


class _PacedPopper:
    """Returns pre-computed frames one per call (like pop_tick)."""

    def __init__(self, frames: list[tuple[int, np.ndarray]]) -> None:
        self._frames = frames
        self._idx = 0

    def __call__(self) -> dict[int, np.ndarray]:
        if self._idx < len(self._frames):
            uid, mono = self._frames[self._idx]
            self._idx += 1
            return {uid: mono}
        return {}


@pytest.mark.asyncio
async def test_dave_to_rtp_e2e(file_regression):
    """Full E2E: opus encode → decode → paced popper → RTP/UDP → golden file."""
    # Step 1: Prepare audio frames
    frames = resample_to_48k_frames(FIXTURES / "speech_sample.wav")

    # Step 2: Opus encode → decode → mono
    encoder = discord.opus.Encoder()
    decoder = discord.opus.Decoder()
    user_id = 42

    mono_frames: list[tuple[int, np.ndarray]] = []
    for pcm_frame in frames:
        opus_data = encoder.encode(pcm_frame, 960)
        decoded_pcm = decoder.decode(opus_data, fec=False)
        mono_frames.append((user_id, stereo_to_mono(decoded_pcm)))

    # Step 3: Send over real UDP via rtp_send_loop with paced popper
    popper = _PacedPopper(mono_frames)

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
            popper,
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

    # Step 4: Collect, decode, compare
    received_ulaw = b""
    for pkt in collector.packets[:expected_count]:
        received_ulaw += _parse_rtp_payload(pkt)

    pcm_8k = ulaw_to_pcm(received_ulaw)
    wav_bytes = pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)

    # Opus encode/decode amplifies soxr platform jitter; use wider thresholds
    # than the direct-PCM golden tests (observed cross-platform RMSE ~108 on
    # Ubuntu, ~311 on Arch due to libopus/soxr build differences).
    check_fn = partial(wav_samples_check, max_rmse=350.0, min_correlation=0.98)
    file_regression.check(wav_bytes, binary=True, extension=".wav", check_fn=check_fn)
