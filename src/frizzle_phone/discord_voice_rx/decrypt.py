"""Packet decryption for Discord voice receive."""

from __future__ import annotations

from typing import TYPE_CHECKING

import nacl.secret

if TYPE_CHECKING:
    from .rtp import RtpPacket


class PacketDecryptor:
    """Decrypts Discord voice RTP packets.

    Supports 4 encryption modes used by Discord:
    - xsalsa20_poly1305
    - xsalsa20_poly1305_suffix
    - xsalsa20_poly1305_lite
    - aead_xchacha20_poly1305_rtpsize
    """

    supported_modes = [
        "aead_xchacha20_poly1305_rtpsize",
        "xsalsa20_poly1305_lite",
        "xsalsa20_poly1305_suffix",
        "xsalsa20_poly1305",
    ]

    def __init__(self, mode: str, secret_key: bytes) -> None:
        self.mode = mode
        try:
            self.decrypt_rtp = getattr(self, "_decrypt_rtp_" + mode)
        except AttributeError as e:
            raise NotImplementedError(mode) from e
        self._box = self._make_box(secret_key)

    def _make_box(self, secret_key: bytes) -> nacl.secret.SecretBox | nacl.secret.Aead:
        if self.mode.startswith("aead"):
            return nacl.secret.Aead(secret_key)
        else:
            return nacl.secret.SecretBox(secret_key)

    def update_secret_key(self, secret_key: bytes) -> None:
        self._box = self._make_box(secret_key)

    def _decrypt_rtp_xsalsa20_poly1305(self, packet: RtpPacket) -> bytes:
        nonce = bytearray(24)
        nonce[:12] = packet.header
        result = self._box.decrypt(bytes(packet.data), bytes(nonce))

        if packet.extended:
            offset = packet.update_ext_headers(result)
            result = result[offset:]

        return result

    def _decrypt_rtp_xsalsa20_poly1305_suffix(self, packet: RtpPacket) -> bytes:
        nonce = packet.data[-24:]
        voice_data = packet.data[:-24]
        result = self._box.decrypt(bytes(voice_data), bytes(nonce))

        if packet.extended:
            offset = packet.update_ext_headers(result)
            result = result[offset:]

        return result

    def _decrypt_rtp_xsalsa20_poly1305_lite(self, packet: RtpPacket) -> bytes:
        nonce = bytearray(24)
        nonce[:4] = packet.data[-4:]
        voice_data = packet.data[:-4]
        result = self._box.decrypt(bytes(voice_data), bytes(nonce))

        if packet.extended:
            offset = packet.update_ext_headers(result)
            result = result[offset:]

        return result

    def _decrypt_rtp_aead_xchacha20_poly1305_rtpsize(self, packet: RtpPacket) -> bytes:
        packet.adjust_rtpsize()

        nonce = bytearray(24)
        nonce[:4] = packet.nonce
        voice_data = packet.data

        assert isinstance(self._box, nacl.secret.Aead)
        result = self._box.decrypt(
            bytes(voice_data), bytes(packet.header), bytes(nonce)
        )

        if packet.extended:
            offset = packet.update_ext_headers(result)
            result = result[offset:]

        return result


def dave_decrypt(
    *,
    dave_session: object | None,
    ssrc_to_id: dict[int, int],
    ssrc: int,
    transport_decrypted: bytes,
) -> bytes:
    """Apply DAVE (Discord Audio Video Encryption) if session is ready."""
    if dave_session is None or not dave_session.ready:  # type: ignore[union-attr]
        return transport_decrypted
    uid = ssrc_to_id.get(ssrc)
    if uid is None:
        return transport_decrypted
    import davey

    return dave_session.decrypt(uid, davey.MediaType.audio, transport_decrypted)  # type: ignore[union-attr]
