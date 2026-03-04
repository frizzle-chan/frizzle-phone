"""RTP receive protocol — extracts PCMU payloads from incoming RTP packets."""

import asyncio
import contextlib
import logging
import queue

import numpy as np
import soxr

from frizzle_phone.bridge import (
    DISCORD_FRAME_SAMPLES,
    DISCORD_SAMPLE_RATE,
    ChunkedResampler,
)
from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp import pcmu
from frizzle_phone.rtp.pcmu import ulaw_to_pcm

logger = logging.getLogger(__name__)


def _mono_to_stereo(mono: np.ndarray) -> bytes:
    """Duplicate mono int16 samples to stereo (interleaved L=R)."""
    return np.repeat(mono, 2).tobytes()


class RtpReceiveProtocol(asyncio.DatagramProtocol):
    """Receives RTP from the phone, decodes PCMU, resamples, enqueues for Discord."""

    def __init__(
        self,
        phone_to_discord_queue: queue.Queue[bytes],
        *,
        stats: BridgeStats | None = None,
    ) -> None:
        self._queue = phone_to_discord_queue
        self._transport: asyncio.DatagramTransport | None = None
        self._stats = stats
        self._resampler = ChunkedResampler(
            pcmu.SAMPLE_RATE,
            DISCORD_SAMPLE_RATE,
            DISCORD_FRAME_SAMPLES,
            quality=soxr.LQ,
        )

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 12:
            return
        # Parse variable-length RTP header
        cc = data[0] & 0x0F
        has_ext = bool(data[0] & 0x10)
        offset = 12 + cc * 4
        if has_ext and len(data) > offset + 4:
            ext_len = int.from_bytes(data[offset + 2 : offset + 4], "big")
            offset += 4 + ext_len * 4
        payload = data[offset:]
        if not payload:
            return

        if self._stats:
            self._stats.record_p2d_recv()

        # Decode PCMU → int16 PCM (8kHz mono)
        pcm_8k = ulaw_to_pcm(payload)
        # Resample 8kHz → 48kHz, yielding fixed-size 960-sample chunks
        arr_8k = np.frombuffer(pcm_8k, dtype=np.int16)
        for arr_48k in self._resampler.feed(arr_8k):
            # Mono → stereo
            stereo = _mono_to_stereo(arr_48k)

            # Enqueue for Discord — drop oldest on overflow to preserve freshness
            # for real-time playback.
            try:
                self._queue.put_nowait(stereo)
            except queue.Full:
                if self._stats:
                    self._stats.p2d_queue_overflow += 1
                    logger.warning(
                        "bridge p2d queue full, dropping oldest (depth=%d)",
                        self._queue.qsize(),
                    )
                with contextlib.suppress(queue.Empty):
                    self._queue.get_nowait()
                self._queue.put_nowait(stereo)

    def get_transport(self) -> asyncio.DatagramTransport | None:
        return self._transport
