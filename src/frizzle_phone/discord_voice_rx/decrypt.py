"""Packet decryption for Discord voice receive."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nacl.secret

log = logging.getLogger(__name__)

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

    @staticmethod
    def _strip_ext(packet: RtpPacket, result: bytes) -> bytes:
        if packet.extended:
            offset = packet.update_ext_headers(result)
            return result[offset:]
        return result

    def _decrypt_rtp_xsalsa20_poly1305(self, packet: RtpPacket) -> bytes:
        nonce = bytearray(24)
        nonce[:12] = packet.header
        result = self._box.decrypt(bytes(packet.data), bytes(nonce))
        return self._strip_ext(packet, result)

    def _decrypt_rtp_xsalsa20_poly1305_suffix(self, packet: RtpPacket) -> bytes:
        nonce = packet.data[-24:]
        voice_data = packet.data[:-24]
        result = self._box.decrypt(bytes(voice_data), bytes(nonce))
        return self._strip_ext(packet, result)

    def _decrypt_rtp_xsalsa20_poly1305_lite(self, packet: RtpPacket) -> bytes:
        nonce = bytearray(24)
        nonce[:4] = packet.data[-4:]
        voice_data = packet.data[:-4]
        result = self._box.decrypt(bytes(voice_data), bytes(nonce))
        return self._strip_ext(packet, result)

    def _decrypt_rtp_aead_xchacha20_poly1305_rtpsize(self, packet: RtpPacket) -> bytes:
        packet.adjust_rtpsize()

        nonce = bytearray(24)
        nonce[:4] = packet.nonce
        voice_data = packet.data

        # Constructor guarantees Aead for aead_* modes; narrow for type checker.
        box = self._box
        if not isinstance(box, nacl.secret.Aead):
            raise RuntimeError(f"expected Aead box for {self.mode}, got {type(box)}")
        result = box.decrypt(bytes(voice_data), bytes(packet.header), bytes(nonce))
        return self._strip_ext(packet, result)


_DAVE_MAGIC = b"\xfa\xfa"
_DAVE_LOG_INTERVAL_S = 5.0
_dave_fail_state: dict[str, float | int] = {"next_log": 0.0, "suppressed": 0}


def dave_decrypt(
    *,
    dave_session: object | None,
    ssrc_to_id: dict[int, int],
    ssrc: int,
    transport_decrypted: bytes,
) -> bytes | None:
    """Apply DAVE (Discord Audio Video Encryption) if session is ready.

    Returns None when DAVE is active but decryption fails, signalling the
    caller to use packet-loss concealment instead of feeding encrypted
    bytes to the Opus decoder.
    """
    if dave_session is None or not dave_session.ready:  # type: ignore[union-attr]
        return transport_decrypted
    uid = ssrc_to_id.get(ssrc)
    if uid is None:
        return transport_decrypted
    try:
        return dave_session.decrypt(uid, _davey().MediaType.audio, transport_decrypted)  # type: ignore[union-attr]
    except Exception as exc:
        _log_dave_failure(uid, exc, transport_decrypted)
        return None


def _log_dave_failure(uid: int, exc: Exception, data: bytes) -> None:
    """Log DAVE decrypt failure, rate-limited to once per interval."""
    import time

    now = time.monotonic()
    if now < _dave_fail_state["next_log"]:
        _dave_fail_state["suppressed"] = int(_dave_fail_state["suppressed"]) + 1
        return

    suppressed = int(_dave_fail_state["suppressed"])
    has_marker = data[-2:] == _DAVE_MAGIC
    msg = "DAVE decrypt failed uid=%d: %s (marker=%s, len=%d)"
    args: tuple[object, ...] = (uid, exc, has_marker, len(data))
    if suppressed > 0:
        msg += " [%d suppressed]"
        args = (*args, suppressed)

    log.warning(msg, *args)
    _dave_fail_state["next_log"] = now + _DAVE_LOG_INTERVAL_S
    _dave_fail_state["suppressed"] = 0


def _davey():  # noqa: ANN202
    """Lazy-import davey to avoid hard dependency."""
    global _davey_module  # noqa: PLW0603
    if _davey_module is None:
        import davey

        _davey_module = davey
    return _davey_module


_davey_module = None
