"""Bidirectional audio bridge between SIP/RTP and Discord voice."""

import asyncio
import logging
import queue
import random
import time

import davey
import discord
import numpy as np
import soxr
from discord.ext import voice_recv
from discord.ext.voice_recv import rtp
from discord.ext.voice_recv.reader import AudioReader
from discord.ext.voice_recv.router import PacketRouter
from discord.opus import OpusError
from nacl.exceptions import CryptoError

from frizzle_phone.bridge_stats import BridgeStats
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


def _patched_callback(self: AudioReader, packet_data: bytes) -> None:
    """AudioReader.callback with DAVE decryption injected.

    After transport-layer decryption, applies DAVE decryption using the
    davey session from the voice connection before opus decode.
    """
    packet = rtp_packet = rtcp_packet = None
    try:
        if not rtp.is_rtcp(packet_data):
            packet = rtp_packet = rtp.decode_rtp(packet_data)
            transport_decrypted = self.decryptor.decrypt_rtp(packet)

            # DAVE decryption (if enabled)
            dave_session = self.voice_client._connection.dave_session
            if dave_session and dave_session.ready:
                uid = self.voice_client._ssrc_to_id.get(packet.ssrc)
                if uid is not None:
                    packet.decrypted_data = dave_session.decrypt(
                        uid, davey.MediaType.audio, transport_decrypted
                    )
                else:
                    packet.decrypted_data = transport_decrypted
            else:
                packet.decrypted_data = transport_decrypted
        else:
            packet = rtcp_packet = rtp.decode_rtcp(
                self.decryptor.decrypt_rtcp(packet_data)
            )
            if not isinstance(packet, rtp.ReceiverReportPacket):
                logger.debug("Unexpected RTCP packet: type=%s", packet.type)
    except CryptoError:
        logger.debug("CryptoError decoding packet")
        return
    except Exception:
        if len(packet_data) == 74 and packet_data[1] == 0x02:
            return  # IP discovery packet
        logger.exception("Error unpacking packet")
        return

    if self.error:
        self.stop()
        return
    if not packet:
        return

    if rtcp_packet:
        self.packet_router.feed_rtcp(rtcp_packet)
    elif rtp_packet:
        if (
            rtp_packet.ssrc not in self.voice_client._ssrc_to_id
            and rtp_packet.is_silence()
        ):
            return
        global _last_callback_rtp
        now = time.monotonic()
        if _last_callback_rtp > 0:
            gap = now - _last_callback_rtp
            if gap > 0.040:
                logger.warning("bridge callback rtp gap: %.1fms", gap * 1000)
        _last_callback_rtp = now

        self.speaking_timer.notify(rtp_packet.ssrc)
        try:
            self.packet_router.feed_rtp(rtp_packet)
        except Exception as e:
            logger.exception("Error processing rtp packet")
            self.error = e
            self.stop()


def apply_discord_patches() -> None:
    """Apply monkey-patches to discord-ext-voice-recv for DAVE and error handling.

    Must be called before connecting to Discord voice.
    """
    ver = getattr(voice_recv, "__version__", "unknown")
    if not str(ver).startswith("0.5."):
        logger.warning(
            "discord-ext-voice-recv version %s may be incompatible with "
            "frizzle-phone patches (written for 0.5.x)",
            ver,
        )
    setattr(PacketRouter, "_do_run", _patched_do_run)  # noqa: B010
    setattr(AudioReader, "callback", _patched_callback)  # noqa: B010


SILENCE_FRAME = b"\x00" * 3840  # 20ms of 48kHz stereo s16le silence
ULAW_SILENCE_PAYLOAD = b"\xff" * SAMPLES_PER_PACKET  # 20ms of 8kHz PCMU silence

_last_callback_rtp: float = 0.0


def stereo_to_mono(data: bytes) -> np.ndarray:
    """Convert 48kHz stereo s16le PCM to mono int16 array."""
    stereo = np.frombuffer(data, dtype=np.int16).reshape(-1, 2)
    left = stereo[:, 0].astype(np.int32)
    right = stereo[:, 1].astype(np.int32)
    return ((left + right) >> 1).astype(np.int16)


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

    def stop(self) -> None:
        self._stopped = True


_MIX_BATCH_THRESHOLD = 0.002  # 2ms — micro-batch for multi-speaker mixing
_MIX_STALE_THRESHOLD = 0.060  # 60ms — discard stale frames after silence gap


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
        self._resampler = soxr.ResampleStream(
            48000, 8000, 1, dtype="int16", quality=soxr.QQ
        )

    def wants_opus(self) -> bool:
        return False

    def _enqueue(self, payload: bytes) -> None:
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

        # Group into time slots — new slot starts when a user_key repeats
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
            ulaw_payload = pcm16_to_ulaw(arr_8k.tobytes())
            self._loop.call_soon_threadsafe(self._enqueue, ulaw_payload)

    def write(self, user: discord.User | None, data: voice_recv.VoiceData) -> None:  # type: ignore[override]
        now = time.monotonic()
        if self._stats:
            self._stats.record_d2p_write()

        if self._pending_frames:
            age = now - self._mix_start_time
            if age > _MIX_STALE_THRESHOLD:
                # Stale batch after silence gap — flush then discard
                if self._stats:
                    self._stats.d2p_stale_flush += 1
                self._flush_mix()
                self._pending_frames.clear()
                self._resampler = soxr.ResampleStream(
                    48000, 8000, 1, dtype="int16", quality=soxr.QQ
                )
            elif age > _MIX_BATCH_THRESHOLD:
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
    last_summary = time.monotonic()

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
            now = time.monotonic()
            if now - last_summary >= 5.0:
                stats.log_summary()
                last_summary = now
