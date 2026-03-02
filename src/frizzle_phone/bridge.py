"""Bidirectional audio bridge between SIP/RTP and Discord voice."""

# Naming convention — d2p / p2d:
#   d2p = Discord-to-Phone (audio from Discord voice → SIP/RTP to phone)
#   p2d = Phone-to-Discord (audio from SIP/RTP phone → Discord voice)
# Queue parameters use long form (discord_to_phone_queue), constants use
# short form (D2P_QUEUE_SIZE), and stats fields use short form (d2p_frames_in).

import asyncio
import logging
import queue
import random
import time

import discord
import numpy as np
import soxr
from discord.ext import voice_recv

from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.pcmu import pcm16_arr_to_ulaw
from frizzle_phone.rtp.stream import PTIME_MS, SAMPLES_PER_PACKET, build_rtp_packet

logger = logging.getLogger(__name__)

SILENCE_FRAME = b"\x00" * 3840  # 20ms of 48kHz stereo s16le silence
ULAW_SILENCE_PAYLOAD = b"\xff" * SAMPLES_PER_PACKET  # 20ms of 8kHz PCMU silence


def stereo_to_mono(data: bytes) -> np.ndarray:
    """Convert 48kHz stereo s16le PCM to mono int16 array."""
    stereo = np.frombuffer(data, dtype=np.int16).reshape(-1, 2)
    mixed = stereo[:, 0].astype(np.int32)
    mixed += stereo[:, 1]
    mixed >>= 1
    return mixed.astype(np.int16)


class PhoneAudioSource(discord.AudioSource):
    """Feeds phone audio to Discord voice channel."""

    def __init__(
        self,
        phone_to_discord_queue: queue.Queue[bytes],
        *,
        stats: BridgeStats | None = None,
    ) -> None:
        self._queue = phone_to_discord_queue
        self._stopped = False
        self._stats = stats

    def read(self) -> bytes:
        if self._stopped:
            return b""
        if self._stats:
            self._stats.p2d_reads += 1
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            if self._stats:
                self._stats.p2d_silence_reads += 1
            return SILENCE_FRAME

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._stopped = True


_MIX_BATCH_THRESHOLD_S = 0.002  # 2ms — micro-batch for multi-speaker mixing
_MIX_STALE_THRESHOLD_S = 0.060  # 60ms — discard stale frames after silence gap


class PhoneAudioSink(voice_recv.AudioSink):
    """Receives Discord voice and enqueues ulaw for phone RTP send.

    Multiple speakers are mixed at 48kHz before resampling to 8kHz.
    Frames are accumulated in a list within a micro-batch window and
    flushed using slot-based grouping to handle burst delivery.
    """

    def __init__(
        self,
        discord_to_phone_queue: asyncio.Queue[bytes],
        loop: asyncio.AbstractEventLoop,
        *,
        stats: BridgeStats | None = None,
    ) -> None:
        super().__init__()
        self._queue = discord_to_phone_queue
        self._loop = loop
        self._pending_frames: list[tuple[int, np.ndarray]] = []
        self._mix_start_time: float = 0.0
        self._stats = stats
        self._resampler = self._new_resampler()

    @staticmethod
    def _new_resampler() -> soxr.ResampleStream:
        return soxr.ResampleStream(48000, 8000, 1, dtype="int16", quality=soxr.QQ)

    def wants_opus(self) -> bool:
        return False

    def _enqueue(self, payload: bytes) -> None:
        # Drop newest on overflow — simple backpressure; the next 20ms frame
        # will retry.  (Contrast with p2d in receive.py which drops oldest
        # to preserve freshness for real-time playback.)
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            if self._stats:
                self._stats.d2p_queue_overflow += 1
                logger.warning(
                    "bridge d2p queue full, dropping frame (depth=%d)",
                    self._queue.qsize(),
                )

    def _flush_mix(self) -> None:
        """Group accumulated frames into time slots, mix, resample, and enqueue."""
        if not self._pending_frames:
            return

        # Group into time slots — new slot starts when a user_key repeats.
        # Each 20ms Discord frame produces one entry per speaker, so a repeated
        # user_key means we've crossed into the next time slot and must mix
        # the previous slot separately to avoid double-counting a speaker.
        slots: list[dict[int, np.ndarray]] = []
        current_slot: dict[int, np.ndarray] = {}
        for user_key, mono in self._pending_frames:
            if user_key in current_slot:
                slots.append(current_slot)
                current_slot = {}
            current_slot[user_key] = mono
        if current_slot:
            slots.append(current_slot)

        for slot in slots:
            if self._stats:
                self._stats.d2p_frames_mixed += 1
            if len(slot) == 1:
                mixed = next(iter(slot.values()))
            else:
                mixed = np.clip(
                    np.sum(list(slot.values()), axis=0, dtype=np.int32),
                    -32768,
                    32767,
                ).astype(np.int16)
            arr_8k = self._resampler.resample_chunk(mixed)
            ulaw_payload = pcm16_arr_to_ulaw(arr_8k)
            self._loop.call_soon_threadsafe(self._enqueue, ulaw_payload)

    def write(self, user: discord.User | None, data: voice_recv.VoiceData) -> None:  # type: ignore[override]
        now = time.monotonic()
        if self._stats:
            self._stats.record_d2p_write()

        if self._pending_frames:
            age = now - self._mix_start_time
            if age > _MIX_STALE_THRESHOLD_S:
                # Stale batch after silence gap — flush then discard
                if self._stats:
                    self._stats.d2p_stale_flush += 1
                self._flush_mix()
                self._pending_frames.clear()
                self._resampler = self._new_resampler()
            elif age > _MIX_BATCH_THRESHOLD_S:
                self._flush_mix()
                self._pending_frames.clear()

        if not self._pending_frames:
            self._mix_start_time = now

        user_key = user.id if user is not None else 0
        self._pending_frames.append((user_key, stereo_to_mono(data.pcm)))

    def cleanup(self) -> None:
        self._flush_mix()
        self._pending_frames.clear()


async def rtp_send_loop(
    discord_to_phone_queue: asyncio.Queue[bytes],
    transport: asyncio.DatagramTransport,
    remote_addr: tuple[str, int],
    *,
    stop_event: asyncio.Event,
    stats: BridgeStats | None = None,
) -> None:
    """Dequeue ulaw payloads and send as RTP packets at 20ms intervals."""
    ssrc = random.randint(0, 0xFFFFFFFF)
    seq = random.randint(0, 0xFFFF)
    timestamp = random.randint(0, 0xFFFFFFFF)
    next_send = time.monotonic()
    first = True
    loop = asyncio.get_running_loop()

    while not stop_event.is_set():
        try:
            payload = discord_to_phone_queue.get_nowait()
            is_silence = False
        except asyncio.QueueEmpty:
            payload = ULAW_SILENCE_PAYLOAD
            is_silence = True

        packet = build_rtp_packet(seq, timestamp, ssrc, payload, marker=first)
        transport.sendto(packet, remote_addr)
        first = False
        seq = (seq + 1) & 0xFFFF
        timestamp = (timestamp + SAMPLES_PER_PACKET) & 0xFFFFFFFF

        if stats:
            stats.rtp_frames_sent += 1
            if is_silence:
                stats.rtp_silence_sent += 1
            depth = discord_to_phone_queue.qsize()
            if depth > stats.d2p_queue_depth_max:
                stats.d2p_queue_depth_max = depth

        next_send += PTIME_MS / 1000.0
        now = time.monotonic()
        # Cap drift: if next_send fell >1 ptime behind (e.g. after silence
        # gap with timeout > ptime), snap forward so burst frames get paced.
        if next_send < now - PTIME_MS / 1000.0:
            next_send = now
        sleep_dur = next_send - now
        if sleep_dur > 0:
            await asyncio.sleep(sleep_dur)
            if stats:
                overshoot = time.monotonic() - next_send
                if overshoot > stats.rtp_max_sleep_overshoot:
                    stats.rtp_max_sleep_overshoot = overshoot

        if stats:
            loop.call_soon(stats.maybe_log_and_reset)
