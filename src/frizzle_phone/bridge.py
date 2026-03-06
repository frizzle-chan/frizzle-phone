"""Bidirectional audio bridge between SIP/RTP and Discord voice."""

# Naming convention — d2p / p2d:
#   d2p = Discord-to-Phone (audio from Discord voice → SIP/RTP to phone)
#   p2d = Phone-to-Discord (audio from SIP/RTP phone → Discord voice)
# Queue parameters use long form (phone_to_discord_queue), constants use
# short form (P2D_QUEUE_SIZE), and stats fields use short form (d2p_frames_mixed).

import asyncio
import logging
import queue
import random
import time
from collections import deque
from collections.abc import Callable

import discord
import numpy as np
import soxr

from frizzle_phone.agc import AgcBank
from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats
from frizzle_phone.rtp import pcmu
from frizzle_phone.rtp.pcmu import pcm16_arr_to_ulaw
from frizzle_phone.rtp.stream import PTIME_MS, SAMPLES_PER_PACKET, build_rtp_packet

logger = logging.getLogger(__name__)

SILENCE_FRAME = b"\x00" * 3840  # 20ms of 48kHz stereo s16le silence
ULAW_SILENCE_PAYLOAD = b"\xff" * SAMPLES_PER_PACKET  # 20ms of 8kHz PCMU silence
MAX_SLOT_QUEUE = 50  # 1s max buffer, bounds latency
DISCORD_SAMPLE_RATE = 48000  # discord.py Encoder.SAMPLING_RATE (Opus mandates 48kHz)
DISCORD_FRAME_SAMPLES = DISCORD_SAMPLE_RATE * PTIME_MS // 1000  # 960


def mix_slot(slot: dict[int, np.ndarray]) -> np.ndarray:
    """Mix multiple PCM16 speaker arrays with 1/sqrt(N) gain scaling."""
    if len(slot) == 1:
        return next(iter(slot.values()))
    summed = np.sum(list(slot.values()), axis=0, dtype=np.int32)
    # 1/sqrt(N) gain keeps loudness without harsh clipping.
    gain = 1.0 / np.sqrt(len(slot))
    return np.clip((summed * gain).astype(np.int32), -32768, 32767).astype(np.int16)


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
    pop_tick: Callable[[], dict[int, np.ndarray]],
    transport: asyncio.DatagramTransport,
    remote_addr: tuple[str, int],
    *,
    stop_event: asyncio.Event,
    stats: BridgeStats | None = None,
    voice_recv_stats: VoiceRecvStats | None = None,
) -> None:
    """Pull frames via pop_tick, mix, resample, send RTP at 20ms intervals."""
    ssrc = random.randint(0, 0xFFFFFFFF)
    seq = random.randint(0, 0xFFFF)
    timestamp = random.randint(0, 0xFFFFFFFF)
    next_send = time.monotonic()
    first = True
    loop = asyncio.get_running_loop()
    resampler = _new_resampler()
    was_silent = True
    agc_bank = AgcBank()
    slot_queue: deque[dict[int, np.ndarray]] = deque()
    payload_queue: deque[bytes] = deque()

    while not stop_event.is_set():
        # 1. Pull one tick of per-user frames
        tick = pop_tick()
        if tick:
            slot_queue.append(tick)
            while len(slot_queue) > MAX_SLOT_QUEUE:
                slot_queue.popleft()
                if stats:
                    stats.d2p_frames_dropped += 1

        if stats:
            stats.d2p_queue_depth = max(stats.d2p_queue_depth, len(slot_queue))

        # 2. Consume one slot per tick — matches the 1-packet-per-tick send
        #    cadence so slot_queue drains at the same rate it fills.  The old
        #    `while not payload_queue` loop starved consumption whenever the
        #    LQ resampler emitted >1 chunk, causing unbounded queue growth.
        fed_this_tick = False
        if slot_queue:
            slot = slot_queue.popleft()
            fed_this_tick = True
            was_silent = False

            slot = agc_bank.process_slot(slot)
            if stats:
                stats.d2p_frames_mixed += 1
            mixed = mix_slot(slot)

            for chunk_8k in resampler.feed(mixed):
                payload_queue.append(pcm16_arr_to_ulaw(chunk_8k))

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
            agc_bank.expire_stale()
        if voice_recv_stats:
            loop.call_soon(voice_recv_stats.maybe_log_and_reset)
