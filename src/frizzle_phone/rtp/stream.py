"""RTP packet construction and async UDP send loop."""

import asyncio
import logging
import random
import struct
import time

logger = logging.getLogger(__name__)

RTP_VERSION = 2
PAYLOAD_TYPE_PCMU = 0
PTIME_MS = 20
SAMPLES_PER_PACKET = 160  # 8000 Hz * 20ms


def build_rtp_packet(
    seq: int, timestamp: int, ssrc: int, payload: bytes, *, marker: bool = False
) -> bytes:
    """Build a 12-byte RTP header + payload."""
    second_byte = PAYLOAD_TYPE_PCMU | (0x80 if marker else 0)
    header = struct.pack(
        "!BBHII",
        (RTP_VERSION << 6) | 0,  # V=2, P=0, X=0, CC=0
        second_byte,  # M bit + PT
        seq & 0xFFFF,
        timestamp & 0xFFFFFFFF,
        ssrc & 0xFFFFFFFF,
    )
    return header + payload


class RtpStream:
    """Sends pre-rendered audio over RTP/UDP at 20ms intervals."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        remote_addr: tuple[str, int],
        audio_buf: bytes,
        done_callback: asyncio.Future[None] | None = None,
        local_port: int = 0,
    ):
        self._loop = loop
        self._remote_addr = remote_addr
        self._audio_buf = audio_buf
        self._done_future = done_callback
        self._local_port = local_port
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task[None] | None = None
        self._ssrc = random.randint(0, 0xFFFFFFFF)
        self._initial_seq = random.randint(0, 0xFFFF)
        self._initial_timestamp = random.randint(0, 0xFFFFFFFF)

    async def start(self) -> None:
        local_addr = ("0.0.0.0", self._local_port) if self._local_port else None
        transport, _ = await self._loop.create_datagram_endpoint(
            asyncio.DatagramProtocol,
            remote_addr=self._remote_addr,
            local_addr=local_addr,
        )
        self._transport = transport  # pyright: ignore[reportAssignmentType]
        self._task = self._loop.create_task(self._send_loop())
        logger.info("RTP stream started to %s", self._remote_addr)

    async def _send_loop(self) -> None:
        seq = self._initial_seq
        timestamp = self._initial_timestamp
        offset = 0
        buf_len = len(self._audio_buf)
        first_packet = True

        next_send_time = time.monotonic()

        while offset + SAMPLES_PER_PACKET <= buf_len:
            payload = self._audio_buf[offset : offset + SAMPLES_PER_PACKET]
            packet = build_rtp_packet(
                seq, timestamp, self._ssrc, payload, marker=first_packet
            )
            if self._transport is not None:
                self._transport.sendto(packet)
            first_packet = False
            seq = (seq + 1) & 0xFFFF
            timestamp = (timestamp + SAMPLES_PER_PACKET) & 0xFFFFFFFF
            offset += SAMPLES_PER_PACKET

            # Wall-clock timing: sleep until absolute deadline (Bug 18)
            next_send_time += PTIME_MS / 1000.0
            sleep_duration = next_send_time - time.monotonic()
            if sleep_duration > 0:
                await asyncio.sleep(sleep_duration)

        packets_sent = (seq - self._initial_seq) & 0xFFFF
        logger.info("RTP stream finished (%d packets sent)", packets_sent)
        if self._done_future is not None and not self._done_future.done():
            self._done_future.set_result(None)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        # Resolve done_future so callbacks don't hang
        if self._done_future is not None and not self._done_future.done():
            self._done_future.cancel()
        logger.info("RTP stream stopped")
