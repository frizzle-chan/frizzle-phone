"""RTP receive protocol — extracts PCMU payloads from incoming RTP packets."""

import asyncio
import contextlib
import logging
import queue

import numpy as np
import soxr

from frizzle_phone.rtp.pcmu import ulaw_to_pcm

logger = logging.getLogger(__name__)


def _mono_to_stereo(mono: bytes) -> bytes:
    """Duplicate mono s16le samples to stereo (interleaved L=R)."""
    out = bytearray(len(mono) * 2)
    for i in range(0, len(mono), 2):
        out[i * 2 : i * 2 + 2] = mono[i : i + 2]
        out[i * 2 + 2 : i * 2 + 4] = mono[i : i + 2]
    return bytes(out)


class RtpReceiveProtocol(asyncio.DatagramProtocol):
    """Receives RTP from the phone, decodes PCMU, resamples, enqueues for Discord."""

    def __init__(self, phone_to_discord_queue: queue.Queue[bytes]) -> None:
        self._queue = phone_to_discord_queue
        self._transport: asyncio.DatagramTransport | None = None

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

        # Decode PCMU → int16 PCM (8kHz mono)
        pcm_8k = ulaw_to_pcm(payload)
        # Resample 8kHz → 48kHz
        arr_8k = np.frombuffer(pcm_8k, dtype=np.int16)
        arr_48k = soxr.resample(arr_8k, 8000, 48000)
        pcm_48k = arr_48k.astype(np.int16).tobytes()
        # Mono → stereo
        stereo = _mono_to_stereo(pcm_48k)

        # Enqueue for Discord (drop oldest on overflow)
        try:
            self._queue.put_nowait(stereo)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            self._queue.put_nowait(stereo)

    def get_transport(self) -> asyncio.DatagramTransport | None:
        return self._transport
