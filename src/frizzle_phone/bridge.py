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
from discord.ext.voice_recv.router import PacketRouter
from discord.opus import OpusError

from frizzle_phone.rtp.pcmu import pcm16_to_ulaw
from frizzle_phone.rtp.stream import PTIME_MS, SAMPLES_PER_PACKET, build_rtp_packet

logger = logging.getLogger(__name__)


def _patched_do_run(self: PacketRouter) -> None:
    """PacketRouter._do_run that skips corrupt opus packets instead of crashing.

    Workaround for https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/43
    """
    while not self._end_thread.is_set():
        self.waiter.wait()
        with self._lock:
            for decoder in self.waiter.items:
                try:
                    data = decoder.pop_data()
                except OpusError:
                    logger.warning(
                        "Skipping corrupt opus packet (ssrc=%s)",
                        decoder.ssrc,
                    )
                    continue
                if data is not None:
                    self.sink.write(data.source, data)


PacketRouter._do_run = _patched_do_run  # type: ignore[assignment]

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


_MIX_BATCH_THRESHOLD = 0.015  # 15ms — separates "same batch" from "new batch"
_MIX_STALE_THRESHOLD = 0.060  # 60ms — discard stale frames after silence gap


class PhoneAudioSink(voice_recv.AudioSink):
    """Receives Discord voice and enqueues ulaw for phone RTP send.

    Multiple speakers are mixed at 48kHz before resampling to 8kHz.
    Frames are accumulated per-user within a ~20ms batch window and
    flushed when the next batch arrives.
    """

    def __init__(
        self,
        discord_to_phone_queue: asyncio.Queue[bytes],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__()
        self._queue = discord_to_phone_queue
        self._loop = loop
        self._pending_frames: dict[int, np.ndarray] = {}
        self._mix_start_time: float = 0.0

    def wants_opus(self) -> bool:
        return False

    def _enqueue(self, payload: bytes) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(payload)

    def _flush_mix(self) -> None:
        """Sum all pending frames, resample to 8kHz, and enqueue as ulaw."""
        if not self._pending_frames:
            return
        mixed = np.sum(
            list(self._pending_frames.values()),
            axis=0,
            dtype=np.int32,
        )
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        arr_8k = soxr.resample(mixed, 48000, 8000)
        ulaw_payload = pcm16_to_ulaw(arr_8k.astype(np.int16).tobytes())
        self._loop.call_soon_threadsafe(self._enqueue, ulaw_payload)

    def write(self, user: discord.User | None, data: voice_recv.VoiceData) -> None:  # type: ignore[override]
        now = time.monotonic()

        if self._pending_frames:
            age = now - self._mix_start_time
            if age > _MIX_STALE_THRESHOLD:
                # Stale batch after silence gap — discard without sending
                self._pending_frames.clear()
            elif age > _MIX_BATCH_THRESHOLD:
                self._flush_mix()
                self._pending_frames.clear()

        if not self._pending_frames:
            self._mix_start_time = now

        mono = stereo_to_mono(data.pcm)
        user_key = user.id if user is not None else 0
        self._pending_frames[user_key] = np.frombuffer(mono, dtype=np.int16)

    def cleanup(self) -> None:
        self._flush_mix()
        self._pending_frames.clear()


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
