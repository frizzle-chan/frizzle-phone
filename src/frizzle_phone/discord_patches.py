"""Monkey-patches for discord-ext-voice-recv: DAVE decryption and error handling."""

import logging
import time

import davey
from discord.ext import voice_recv
from discord.ext.voice_recv import rtp
from discord.ext.voice_recv.reader import AudioReader
from discord.ext.voice_recv.router import PacketRouter
from discord.opus import OpusError
from nacl.exceptions import CryptoError

logger = logging.getLogger(__name__)

_IP_DISCOVERY_LEN = 74
_IP_DISCOVERY_TYPE = 0x02


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

            # DAVE decryption (if enabled) — default to transport-layer,
            # override with DAVE when session is ready and SSRC is mapped.
            packet.decrypted_data = transport_decrypted
            dave_session = self.voice_client._connection.dave_session
            if dave_session and dave_session.ready:
                uid = self.voice_client._ssrc_to_id.get(packet.ssrc)
                if uid is not None:
                    packet.decrypted_data = dave_session.decrypt(
                        uid, davey.MediaType.audio, transport_decrypted
                    )
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
        if (
            len(packet_data) == _IP_DISCOVERY_LEN
            and packet_data[1] == _IP_DISCOVERY_TYPE
        ):
            logger.debug("IP discovery packet (%d bytes)", len(packet_data))
            return
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
        now = time.monotonic()
        last = getattr(self, "_last_callback_rtp", 0.0)
        if last > 0:
            gap = now - last
            if gap > 0.040:
                logger.warning("bridge callback rtp gap: %.1fms", gap * 1000)
        self._last_callback_rtp = now  # type: ignore[attr-defined]  # monkey-patched attribute

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
