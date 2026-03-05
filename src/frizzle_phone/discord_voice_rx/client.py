"""VoiceRecvClient — discord.VoiceClient subclass that receives voice audio."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import discord
from discord.voice_state import VoiceConnectionState

from .decoder import DecoderThread
from .decrypt import PacketDecryptor, dave_decrypt
from .gateway import hook
from .rtp import is_rtcp, parse_rtp
from .stats import VoiceRecvStats

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)


class VoiceRecvClient(discord.VoiceClient):
    """VoiceClient that receives Discord voice audio via pop_tick()."""

    def __init__(
        self, client: discord.Client, channel: discord.abc.Connectable
    ) -> None:
        super().__init__(client, channel)
        self._ssrc_to_id: dict[int, int] = {}
        self._id_to_ssrc: dict[int, int] = {}
        self._decoder_thread: DecoderThread | None = None
        self._decryptor: PacketDecryptor | None = None
        self._recv_stats = VoiceRecvStats()

    def create_connection_state(self) -> VoiceConnectionState:
        return VoiceConnectionState(self, hook=hook)

    def _add_ssrc(self, user_id: int, ssrc: int) -> None:
        self._ssrc_to_id[ssrc] = user_id
        self._id_to_ssrc[user_id] = ssrc
        if self._decoder_thread:
            self._decoder_thread.set_ssrc_user(ssrc, user_id)

    def _remove_ssrc(self, *, user_id: int) -> None:
        ssrc = self._id_to_ssrc.pop(user_id, None)
        if ssrc is not None:
            self._ssrc_to_id.pop(ssrc, None)
            if self._decoder_thread:
                self._decoder_thread.destroy_decoder(ssrc=ssrc, user_id=user_id)

    def _update_secret_key(self) -> None:
        """Update decryptor with the current secret key from the WS."""
        if self._decryptor is not None:
            self._decryptor.update_secret_key(bytes(self.secret_key))

    def start_listening(self) -> None:
        """Create decryptor, start decoder thread, register socket callback."""
        if self._decoder_thread is not None:
            log.warning("Already listening")
            return

        self._decryptor = PacketDecryptor(self.mode, bytes(self.secret_key))
        self._recv_stats = VoiceRecvStats()
        self._decoder_thread = DecoderThread(stats=self._recv_stats)
        self._decoder_thread.start()
        self._connection.add_socket_listener(self._socket_callback_fn)

    def stop_listening(self) -> None:
        """Remove socket listener and stop decoder thread."""
        if self._decoder_thread is None:
            return

        self._connection.remove_socket_listener(self._socket_callback_fn)
        self._decoder_thread.stop()
        self._decoder_thread.join(timeout=2.0)
        self._decoder_thread = None
        self._decryptor = None

    def stop(self) -> None:
        """Stop playing and receiving audio."""
        super().stop()
        self.stop_listening()

    def pop_tick(self) -> dict[int, np.ndarray]:
        """Pull one synchronized frame per active user.

        Returns empty dict if not listening.
        """
        if self._decoder_thread is None:
            return {}
        return self._decoder_thread.pop_tick()

    @property
    def recv_stats(self) -> VoiceRecvStats:
        return self._recv_stats

    def _socket_callback_fn(self, packet_data: bytes) -> None:
        """Socket callback — parse, decrypt, feed to decoder thread."""
        t0 = time.monotonic()
        self._recv_stats.packets_in += 1

        if is_rtcp(packet_data):
            return  # We don't process RTCP

        try:
            packet = parse_rtp(packet_data)
            if self._decryptor is None:
                return
            transport_decrypted = self._decryptor.decrypt_rtp(packet)

            # Apply DAVE decryption if available
            dave_session = getattr(self._connection, "dave_session", None)
            packet.decrypted_data = dave_decrypt(
                dave_session=dave_session,
                ssrc_to_id=self._ssrc_to_id,
                ssrc=packet.ssrc,
                transport_decrypted=transport_decrypted,
            )
        except Exception:
            self._recv_stats.packets_decrypt_failed += 1
            log.debug("Decrypt error", exc_info=True)
            return
        finally:
            elapsed_us = int((time.monotonic() - t0) * 1_000_000)
            if elapsed_us > self._recv_stats.max_callback_us:
                self._recv_stats.max_callback_us = elapsed_us

        # Filter unknown SSRCs — decoder thread drops them anyway
        if packet.ssrc not in self._ssrc_to_id:
            return

        if self._decoder_thread is not None:
            self._decoder_thread.feed(packet.ssrc, packet)
