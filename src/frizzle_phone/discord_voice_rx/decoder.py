"""Jitter buffer, decoder thread, and audio utilities for discord_voice_rx."""

from __future__ import annotations

import contextlib
import heapq
import logging
import queue
import threading
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Any

import numpy as np

from frizzle_phone.audio_utils import stereo_to_mono

if TYPE_CHECKING:
    from .rtp import RtpPacket, _PacketCmpMixin
    from .stats import VoiceRecvStats

log = logging.getLogger(__name__)


class JitterBuffer:
    """Sequence-ordered jitter buffer backed by a min-heap.

    Simplified from discord-ext-voice-recv HeapJitterBuffer.
    """

    def __init__(self, *, prefill: int = 2, maxsize: int = 10) -> None:
        self._heap: list[_PacketCmpMixin] = []
        self._prefill_remaining: int = prefill
        self._prefill: int = prefill
        self._maxsize: int = maxsize
        self._last_seq: int = -1

    def __len__(self) -> int:
        return len(self._heap)

    def push(self, packet: _PacketCmpMixin) -> None:
        heapq.heappush(self._heap, packet)
        if self._prefill_remaining > 0:
            self._prefill_remaining -= 1
        # Drop oldest if over maxsize
        while len(self._heap) > self._maxsize:
            heapq.heappop(self._heap)

    def pop(self) -> _PacketCmpMixin | None:
        if self._prefill_remaining > 0 or not self._heap:
            return None
        pkt = heapq.heappop(self._heap)
        self._last_seq = pkt.sequence
        return pkt

    def gap(self) -> int:
        """Return the sequence gap between last popped and next buffered packet."""
        if self._heap and self._last_seq >= 0:
            return (self._heap[0].sequence - self._last_seq - 1 + 65536) % 65536
        return 0

    def reset(self) -> None:
        self._heap.clear()
        self._prefill_remaining = self._prefill
        self._last_seq = -1

    def flush(self) -> list[_PacketCmpMixin]:
        """Drain all packets in sequence order, resetting prefill."""
        packets = sorted(self._heap)
        self._heap.clear()
        if packets:
            self._last_seq = packets[-1].sequence
        self._prefill_remaining = self._prefill
        return packets


_MAX_USER_BUFFER = 50  # Max buffered frames per user (~1s at 20ms/frame)

# Command sentinels for routing through the packet queue
_CMD_SET_SSRC = "_set_ssrc"
_CMD_DESTROY = "_destroy"


class DecoderThread(threading.Thread):
    """Consumes decrypted RTP packets, opus-decodes, and buffers per-user PCM frames.

    The asyncio RTP send loop calls ``pop_tick()`` every 20ms to pull one
    synchronized frame per active user.
    """

    def __init__(self, *, stats: VoiceRecvStats) -> None:
        super().__init__(daemon=True, name=f"voice-rx-decoder-{id(self):x}")
        self._stats = stats
        self._packet_queue: queue.Queue[tuple[Any, ...] | None] = queue.Queue(
            maxsize=500
        )
        self._jitter_buffers: dict[int, JitterBuffer] = defaultdict(
            lambda: JitterBuffer(prefill=2, maxsize=10)
        )
        self._decoders: dict[int, Any] = {}  # ssrc -> opus.Decoder
        self._user_buffers: dict[int, deque[np.ndarray]] = defaultdict(
            lambda: deque(maxlen=_MAX_USER_BUFFER)
        )
        self._ssrc_to_user: dict[int, int] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def feed(self, ssrc: int, packet: RtpPacket) -> None:
        """Called from socket callback thread. Drops if queue is full."""
        with contextlib.suppress(queue.Full):
            self._packet_queue.put_nowait((ssrc, packet))

    def set_ssrc_user(self, ssrc: int, user_id: int) -> None:
        """Update SSRC→user_id mapping (routed through queue for thread safety)."""
        with contextlib.suppress(queue.Full):
            self._packet_queue.put_nowait((_CMD_SET_SSRC, ssrc, user_id))

    def destroy_decoder(self, *, ssrc: int, user_id: int | None = None) -> None:
        """Clean up per-SSRC state and user buffer (routed through queue)."""
        with contextlib.suppress(queue.Full):
            self._packet_queue.put_nowait((_CMD_DESTROY, ssrc, user_id))

    def pop_tick(self) -> dict[int, np.ndarray]:
        """Pop one frame per user. Called from the RTP send loop every 20ms."""
        result: dict[int, np.ndarray] = {}
        with self._lock:
            for user_id in list(self._user_buffers):
                buf = self._user_buffers[user_id]
                if buf:
                    result[user_id] = buf.popleft()
                if not buf:
                    del self._user_buffers[user_id]
        if result:
            self._stats.ticks_served += 1
        else:
            self._stats.ticks_empty += 1
        return result

    def stop(self) -> None:
        """Signal the decoder thread to exit."""
        self._stop_event.set()
        self._packet_queue.put(None)  # unblock get()

    def _handle_command(self, cmd: tuple[Any, ...]) -> None:
        """Process a command tuple on the decoder thread."""
        if cmd[0] == _CMD_SET_SSRC:
            _, ssrc, user_id = cmd
            self._ssrc_to_user[ssrc] = user_id
        elif cmd[0] == _CMD_DESTROY:
            _, ssrc, user_id = cmd
            self._jitter_buffers.pop(ssrc, None)
            self._decoders.pop(ssrc, None)
            self._ssrc_to_user.pop(ssrc, None)
            if user_id is not None:
                with self._lock:
                    self._user_buffers.pop(user_id, None)

    def run(self) -> None:
        """Thread main loop: dequeue packets, jitter-buffer, opus decode, buffer."""
        from discord.opus import Decoder as OpusDecoder

        while not self._stop_event.is_set():
            try:
                item = self._packet_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is None:
                break

            # Handle command tuples routed through the queue
            if isinstance(item[0], str):
                self._handle_command(item)
                continue

            ssrc, packet = item
            user_id = self._ssrc_to_user.get(ssrc)
            if user_id is None:
                continue

            jbuf = self._jitter_buffers[ssrc]
            jbuf.push(packet)

            # Pop all ready packets from jitter buffer
            while True:
                ready = jbuf.pop()
                if ready is None:
                    break

                t0 = time.monotonic()
                try:
                    decoder = self._decoders.get(ssrc)
                    if decoder is None:
                        decoder = OpusDecoder()
                        self._decoders[ssrc] = decoder

                    opus_data = ready.decrypted_data
                    if opus_data is None:
                        # No payload: decode with packet loss concealment
                        pcm = decoder.decode(None, fec=False)
                    else:
                        pcm = decoder.decode(opus_data, fec=False)

                    self._stats.opus_decodes += 1
                except Exception:
                    log.warning("Opus decode error for ssrc=%s", ssrc, exc_info=True)
                    self._stats.opus_errors += 1
                    continue
                finally:
                    elapsed_us = int((time.monotonic() - t0) * 1_000_000)
                    if elapsed_us > self._stats.max_decode_us:
                        self._stats.max_decode_us = elapsed_us

                mono = stereo_to_mono(pcm)
                with self._lock:
                    self._user_buffers[user_id].append(mono)
