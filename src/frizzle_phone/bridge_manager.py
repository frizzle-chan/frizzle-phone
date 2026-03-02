"""Manages bidirectional audio bridges between SIP/RTP and Discord voice."""

from __future__ import annotations

import asyncio
import logging
import queue

from discord.ext import voice_recv

from frizzle_phone.bridge import PhoneAudioSink, PhoneAudioSource, rtp_send_loop
from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.receive import RtpReceiveProtocol

logger = logging.getLogger(__name__)

P2D_QUEUE_SIZE = 15  # Phone-to-Discord: ~300ms at 20ms/frame
D2P_QUEUE_SIZE = 50  # Discord-to-Phone: ~1s at 20ms/frame


class BridgeHandle:
    """Opaque handle returned by BridgeManager for later teardown."""

    def __init__(
        self,
        stop_event: asyncio.Event,
        send_task: asyncio.Task[None],
        rtp_transport: asyncio.DatagramTransport,
        voice_client: voice_recv.VoiceRecvClient,
        sink: PhoneAudioSink,
    ) -> None:
        self._stop_event = stop_event
        self._send_task = send_task
        self._rtp_transport = rtp_transport
        self._voice_client = voice_client
        self._sink = sink

    def stop(self) -> None:
        """Tear down the bridge. Idempotent."""
        self._stop_event.set()
        self._send_task.cancel()
        self._rtp_transport.close()
        self._voice_client.stop()
        self._sink.cleanup()


class BridgeManager:
    """Creates and tears down bidirectional audio bridges."""

    def __init__(self) -> None:
        self._bridge_tasks: set[asyncio.Task[None]] = set()

    async def start(
        self,
        voice_client: voice_recv.VoiceRecvClient,
        rtp_port: int,
        remote_rtp_addr: tuple[str, int],
    ) -> BridgeHandle:
        """Set up a bidirectional audio bridge.

        Returns a BridgeHandle for later teardown via handle.stop().
        """
        loop = asyncio.get_running_loop()

        phone_q: queue.Queue[bytes] = queue.Queue(maxsize=P2D_QUEUE_SIZE)
        discord_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=D2P_QUEUE_SIZE)
        stats = BridgeStats()

        # Bind RTP receive on the port advertised in SDP
        rtp_transport, _ = await loop.create_datagram_endpoint(
            lambda: RtpReceiveProtocol(phone_q, stats=stats),
            local_addr=("0.0.0.0", rtp_port),
        )

        # Phone -> Discord
        source = PhoneAudioSource(phone_q, stats=stats)
        voice_client.play(source)

        # Discord -> Phone
        sink = PhoneAudioSink(discord_q, loop, stats=stats)
        voice_client.listen(sink)

        # RTP send loop
        stop_event = asyncio.Event()
        send_task = loop.create_task(
            rtp_send_loop(
                discord_q,
                rtp_transport,
                remote_rtp_addr,
                stop_event=stop_event,
                stats=stats,
            ),
            name=f"rtp-send-{rtp_port}",
        )
        self._bridge_tasks.add(send_task)
        send_task.add_done_callback(self._bridge_tasks.discard)

        return BridgeHandle(
            stop_event=stop_event,
            send_task=send_task,
            rtp_transport=rtp_transport,
            voice_client=voice_client,
            sink=sink,
        )

    def shutdown(self) -> None:
        """Cancel all bridge tasks (called during server shutdown)."""
        for task in self._bridge_tasks:
            task.cancel()
        self._bridge_tasks.clear()
