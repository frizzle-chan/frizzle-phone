"""Bidirectional audio bridge between SIP/RTP and Discord voice."""

# Naming convention — d2p / p2d:
#   d2p = Discord-to-Phone (audio from Discord voice → SIP/RTP to phone)
#   p2d = Phone-to-Discord (audio from SIP/RTP phone → Discord voice)
# Queue parameters use long form (phone_to_discord_queue), constants use
# short form (P2D_QUEUE_SIZE), and stats fields use short form (d2p_frames_in).

import asyncio
import logging
import queue
import random
import threading
import time
from collections import deque

import discord
import numpy as np
import soxr
from discord.ext import voice_recv

from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp import pcmu
from frizzle_phone.rtp.pcmu import pcm16_arr_to_ulaw
from frizzle_phone.rtp.stream import PTIME_MS, SAMPLES_PER_PACKET, build_rtp_packet

logger = logging.getLogger(__name__)

SILENCE_FRAME = b"\x00" * 3840  # 20ms of 48kHz stereo s16le silence
ULAW_SILENCE_PAYLOAD = b"\xff" * SAMPLES_PER_PACKET  # 20ms of 8kHz PCMU silence
MAX_SLOT_QUEUE = 50  # 1s max buffer, bounds latency
DISCORD_SAMPLE_RATE = 48000  # discord.py Encoder.SAMPLING_RATE (Opus mandates 48kHz)
DISCORD_FRAME_SAMPLES = DISCORD_SAMPLE_RATE * PTIME_MS // 1000  # 960


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


class PhoneAudioSink(voice_recv.AudioSink):
    """Receives Discord voice and accumulates raw mono frames for mixing.

    The rtp_send_loop drains accumulated frames each 20ms tick and performs
    slot-based mixing, resampling, and RTP encoding — keeping mixing on a
    strict cadence instead of the bursty write() thread.
    """

    def __init__(self, *, stats: BridgeStats | None = None) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._buf: list[tuple[int, np.ndarray]] = []
        self._stats = stats

    def wants_opus(self) -> bool:
        return False

    def drain(self) -> list[tuple[int, np.ndarray]]:
        """Swap out accumulated frames (thread-safe). Returns the old buffer."""
        with self._lock:
            frames = self._buf
            self._buf = []
        return frames

    def write(self, user: discord.User | None, data: voice_recv.VoiceData) -> None:  # type: ignore[override]
        if self._stats:
            self._stats.record_d2p_write()
        user_key = user.id if user is not None else 0
        mono = stereo_to_mono(data.pcm)
        with self._lock:
            self._buf.append((user_key, mono))

    def cleanup(self) -> None:
        self.drain()  # discard remaining frames


class ChunkedResampler:
    """Wraps soxr.ResampleStream to emit fixed-size output chunks.

    Sinc-based soxr quality levels (LQ+) buffer internally and emit in
    bursts rather than a steady 1:ratio output.  This wrapper accumulates
    resampler output and yields exactly ``chunk_size`` samples at a time.
    """

    __slots__ = (
        "_in_rate",
        "_out_rate",
        "_quality",
        "_chunk_size",
        "_resampler",
        "_buf",
    )

    def __init__(
        self, in_rate: int, out_rate: int, chunk_size: int, *, quality: int = soxr.LQ
    ) -> None:
        self._in_rate = in_rate
        self._out_rate = out_rate
        self._quality = quality
        self._chunk_size = chunk_size
        self._resampler = soxr.ResampleStream(
            in_rate, out_rate, 1, dtype="int16", quality=quality
        )
        self._buf = np.empty(0, dtype=np.int16)

    def feed(self, samples: np.ndarray) -> list[np.ndarray]:
        """Feed input samples, return 0+ fixed-size output chunks."""
        out = self._resampler.resample_chunk(samples)
        if len(out) > 0:
            self._buf = np.concatenate([self._buf, out]) if len(self._buf) else out
        chunks: list[np.ndarray] = []
        while len(self._buf) >= self._chunk_size:
            chunks.append(self._buf[: self._chunk_size])
            self._buf = self._buf[self._chunk_size :]
        return chunks

    def clear(self) -> None:
        """Reset resampler state and discard buffered output."""
        self._resampler = soxr.ResampleStream(
            self._in_rate, self._out_rate, 1, dtype="int16", quality=self._quality
        )
        self._buf = np.empty(0, dtype=np.int16)


def _new_resampler() -> ChunkedResampler:
    # LQ: sinc FIR with ~96dB stopband — needed to prevent aliasing on
    # 6:1 decimation.  QQ has no anti-alias filter; HQ adds too much
    # group delay (~140ms) for interactive voice.  See DESIGN.md#resampling.
    return ChunkedResampler(
        DISCORD_SAMPLE_RATE, pcmu.SAMPLE_RATE, SAMPLES_PER_PACKET, quality=soxr.LQ
    )


async def rtp_send_loop(
    sink: PhoneAudioSink,
    transport: asyncio.DatagramTransport,
    remote_addr: tuple[str, int],
    *,
    stop_event: asyncio.Event,
    stats: BridgeStats | None = None,
) -> None:
    """Drain sink, mix, resample, and send RTP packets at 20ms intervals."""
    ssrc = random.randint(0, 0xFFFFFFFF)
    seq = random.randint(0, 0xFFFF)
    timestamp = random.randint(0, 0xFFFFFFFF)
    next_send = time.monotonic()
    first = True
    loop = asyncio.get_running_loop()
    resampler = _new_resampler()
    was_silent = True
    slot_queue: deque[dict[int, np.ndarray]] = deque()
    payload_queue: deque[bytes] = deque()

    while not stop_event.is_set():
        # 1. Drain new frames into slots
        frames = sink.drain()
        if frames:
            current_slot: dict[int, np.ndarray] = {}
            for user_key, mono in frames:
                if user_key in current_slot:
                    slot_queue.append(current_slot)
                    current_slot = {}
                current_slot[user_key] = mono
            if current_slot:
                slot_queue.append(current_slot)

            # Cap queue — drop oldest if overflowing
            while len(slot_queue) > MAX_SLOT_QUEUE:
                slot_queue.popleft()
                if stats:
                    stats.d2p_frames_dropped += 1

        if stats:
            stats.d2p_queue_depth = max(stats.d2p_queue_depth, len(slot_queue))

        # 2. Feed slots to resampler until we have a payload or run out
        fed_this_tick = False
        while slot_queue and not payload_queue:
            slot = slot_queue.popleft()
            fed_this_tick = True

            if stats:
                stats.d2p_frames_mixed += 1
            if len(slot) == 1:
                mixed = next(iter(slot.values()))
            else:
                mixed = np.clip(
                    np.sum(list(slot.values()), axis=0, dtype=np.int32),
                    -32768,
                    32767,
                ).astype(np.int16)

            for chunk_8k in resampler.feed(mixed):
                payload_queue.append(pcm16_arr_to_ulaw(chunk_8k))

        if fed_this_tick:
            was_silent = False

        # 3. Send one payload or silence
        if payload_queue:
            payload = payload_queue.popleft()
            is_silence = False
        else:
            payload = ULAW_SILENCE_PAYLOAD
            is_silence = True
            # Only reset the resampler on a true silence transition — no
            # slots were consumed this tick.  During the sinc filter's
            # priming period (fed_this_tick=True but no output yet), keep
            # the filter state so it can finish building up history.
            if not was_silent and not fed_this_tick:
                resampler.clear()
            if not fed_this_tick:
                was_silent = True

        packet = build_rtp_packet(seq, timestamp, ssrc, payload, marker=first)
        transport.sendto(packet, remote_addr)
        first = False
        seq = (seq + 1) & 0xFFFF
        timestamp = (timestamp + SAMPLES_PER_PACKET) & 0xFFFFFFFF

        if stats:
            stats.rtp_frames_sent += 1
            if is_silence:
                stats.rtp_silence_sent += 1

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
