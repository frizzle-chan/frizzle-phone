"""Bidirectional audio bridge between SIP/RTP and Discord voice."""

import asyncio
import contextlib
import logging
import queue
import random
import struct
import time

import discord
import numpy as np
import soxr
from discord.ext import voice_recv

from frizzle_phone.rtp.pcmu import pcm16_to_ulaw
from frizzle_phone.rtp.stream import PTIME_MS, SAMPLES_PER_PACKET, build_rtp_packet

logger = logging.getLogger(__name__)

SILENCE_FRAME = b"\x00" * 3840  # 20ms of 48kHz stereo s16le silence
ULAW_SILENCE_PAYLOAD = b"\xff" * SAMPLES_PER_PACKET  # 20ms of 8kHz PCMU silence


def stereo_to_mono(data: bytes) -> bytes:
    """Convert 48kHz stereo s16le PCM to mono by averaging L+R."""
    n_samples = len(data) // 2
    samples = struct.unpack(f"<{n_samples}h", data)
    mono = []
    for i in range(0, n_samples, 2):
        mono.append((samples[i] + samples[i + 1] + 1) // 2)
    return struct.pack(f"<{len(mono)}h", *mono)


class PhoneAudioSource(discord.AudioSource):
    """Feeds phone audio to Discord voice channel."""

    def __init__(self, phone_to_discord_queue: queue.Queue[bytes]) -> None:
        self._queue = phone_to_discord_queue
        self._stopped = False

    def read(self) -> bytes:
        if self._stopped:
            return b""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return SILENCE_FRAME

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._stopped = True

    def stop(self) -> None:
        self._stopped = True


class PhoneAudioSink(voice_recv.AudioSink):
    """Receives Discord voice and enqueues ulaw for phone RTP send."""

    def __init__(self, discord_to_phone_queue: asyncio.Queue[bytes]) -> None:
        super().__init__()
        self._queue = discord_to_phone_queue

    def wants_opus(self) -> bool:
        return False

    def write(self, _user: discord.User | None, data: voice_recv.VoiceData) -> None:  # type: ignore[override]
        pcm_48k_stereo = data.pcm
        mono = stereo_to_mono(pcm_48k_stereo)
        arr_48k = np.frombuffer(mono, dtype=np.int16)
        arr_8k = soxr.resample(arr_48k, 48000, 8000)
        pcm_8k = arr_8k.astype(np.int16).tobytes()
        ulaw_payload = pcm16_to_ulaw(pcm_8k)
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(ulaw_payload)

    def cleanup(self) -> None:
        pass


async def rtp_send_loop(
    discord_to_phone_queue: asyncio.Queue[bytes],
    transport: asyncio.DatagramTransport,
    remote_addr: tuple[str, int],
    *,
    stop_event: asyncio.Event,
) -> None:
    """Dequeue ulaw payloads and send as RTP packets at 20ms intervals."""
    ssrc = random.randint(0, 0xFFFFFFFF)
    seq = random.randint(0, 0xFFFF)
    timestamp = random.randint(0, 0xFFFFFFFF)
    next_send = time.monotonic()
    first = True

    while not stop_event.is_set():
        try:
            payload = await asyncio.wait_for(discord_to_phone_queue.get(), timeout=0.04)
        except TimeoutError:
            payload = ULAW_SILENCE_PAYLOAD

        packet = build_rtp_packet(seq, timestamp, ssrc, payload, marker=first)
        transport.sendto(packet, remote_addr)
        first = False
        seq = (seq + 1) & 0xFFFF
        timestamp = (timestamp + SAMPLES_PER_PACKET) & 0xFFFFFFFF

        next_send += PTIME_MS / 1000.0
        sleep_dur = next_send - time.monotonic()
        if sleep_dur > 0:
            await asyncio.sleep(sleep_dur)
