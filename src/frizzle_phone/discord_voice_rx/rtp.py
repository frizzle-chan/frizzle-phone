"""RTP packet parsing for Discord voice receive.

Ported from discord-ext-voice-recv (https://github.com/imayhaveborkedit/discord-ext-voice-recv).
"""

from __future__ import annotations

import struct
from collections import namedtuple
from typing import Any, Final

OPUS_SILENCE: Final = b"\xf8\xff\xfe"


def is_rtcp(data: bytes) -> bool:
    """Check if a packet is RTCP based on the payload type byte."""
    return 200 <= data[1] <= 204


def parse_rtp(data: bytes) -> RtpPacket:
    """Parse raw bytes into an RtpPacket."""
    return RtpPacket(data)


class _PacketCmpMixin:
    __slots__ = ("ssrc", "sequence", "timestamp")

    ssrc: int
    sequence: int
    timestamp: int
    decrypted_data: bytes | None

    def __lt__(self, other: _PacketCmpMixin) -> bool:
        if self.ssrc != other.ssrc:
            raise TypeError(f"packet ssrc mismatch ({self.ssrc}, {other.ssrc})")
        return self.sequence < other.sequence and self.timestamp < other.timestamp

    def __gt__(self, other: _PacketCmpMixin) -> bool:
        if self.ssrc != other.ssrc:
            raise TypeError(f"packet ssrc mismatch ({self.ssrc}, {other.ssrc})")
        return self.sequence > other.sequence or self.timestamp > other.timestamp

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _PacketCmpMixin):
            return NotImplemented
        if self.ssrc != other.ssrc:
            return False
        return self.sequence == other.sequence and self.timestamp == other.timestamp

    def is_silence(self) -> bool:
        return self.decrypted_data == OPUS_SILENCE


class RtpPacket(_PacketCmpMixin):
    """Parsed RTP packet."""

    __slots__ = (
        "version",
        "padding",
        "extended",
        "cc",
        "marker",
        "payload",
        "sequence",
        "timestamp",
        "ssrc",
        "csrcs",
        "header",
        "data",
        "decrypted_data",
        "nonce",
        "extension",
        "extension_data",
        "_rtpsize",
    )

    _hstruct = struct.Struct(">xxHII")
    _ext_header = namedtuple("Extension", "profile length values")
    _ext_magic = b"\xbe\xde"

    def __init__(self, data: bytes) -> None:
        raw = bytearray(data)

        self.version: int = raw[0] >> 6
        self.padding: bool = bool(raw[0] & 0b00100000)
        self.extended: bool = bool(raw[0] & 0b00010000)
        self.cc: int = raw[0] & 0b00001111

        self.marker: bool = bool(raw[1] & 0b10000000)
        self.payload: int = raw[1] & 0b01111111

        sequence, timestamp, ssrc = self._hstruct.unpack_from(raw)
        self.sequence: int = sequence
        self.timestamp: int = timestamp
        self.ssrc: int = ssrc

        self.csrcs: tuple[int, ...] = ()
        self.extension: Any = None
        self.extension_data: dict[int, bytes] = {}

        self.header: bytearray = raw[:12]
        self.data: bytearray = raw[12:]
        self.decrypted_data: bytes | None = None

        self.nonce: bytearray = bytearray()
        self._rtpsize: bool = False

        if self.cc:
            fmt = f">{self.cc}I"
            offset = struct.calcsize(fmt) + 12
            self.csrcs = struct.unpack(fmt, raw[12:offset])
            self.data = raw[offset:]

    def adjust_rtpsize(self) -> None:
        """Adjust the packet header and data for the rtpsize encryption format."""
        self._rtpsize = True
        self.nonce = self.data[-4:]

        if not self.extended:
            self.data = self.data[:-4]
            return

        # rtpsize formats include the ext header in the RTP header
        # and the nonce is removed from the end
        self.header += self.data[:4]
        self.data = self.data[4:-4]

    def update_ext_headers(self, data: bytes) -> int:
        """Add extended header data to this packet, returns payload offset."""
        if not self.extended:
            return 0

        # rtpsize formats have the extension header in the rtp header
        if self._rtpsize:
            data = bytes(self.header[-4:]) + data

        profile, length = struct.unpack_from(">2sH", data)

        if profile == self._ext_magic:
            self._parse_bede_header(data, length)

        values = struct.unpack(f">{length}I", data[4 : 4 + length * 4])
        self.extension = self._ext_header(profile, length, values)

        offset = 4 + length * 4
        if self._rtpsize:
            offset -= 4

        return offset

    # https://www.rfcreader.com/#rfc5285_line186
    def _parse_bede_header(self, data: bytes, length: int) -> None:
        offset = 4
        n = 0

        while n < length:
            next_byte = data[offset : offset + 1]

            if next_byte == b"\x00":
                offset += 1
                continue

            header = next_byte[0]

            element_id = header >> 4
            element_len = 1 + (header & 0b0000_1111)

            self.extension_data[element_id] = data[
                offset + 1 : offset + 1 + element_len
            ]
            offset += 1 + element_len
            n += 1

    def __repr__(self) -> str:
        return (
            "<RtpPacket "
            f"ssrc={self.ssrc}, "
            f"sequence={self.sequence}, "
            f"timestamp={self.timestamp}, "
            f"size={len(self.data)}, "
            f"ext={set(self.extension_data)}"
            ">"
        )


class FakePacket(_PacketCmpMixin):
    """Synthetic packet used for FEC gap filling."""

    __slots__ = ("ssrc", "sequence", "timestamp")
    decrypted_data: bytes = b""
    extension_data: dict[int, bytes] = {}

    def __init__(self, ssrc: int, sequence: int, timestamp: int) -> None:
        self.ssrc: int = ssrc
        self.sequence: int = sequence
        self.timestamp: int = timestamp

    def __repr__(self) -> str:
        return (
            f"<FakePacket ssrc={self.ssrc}, "
            f"sequence={self.sequence}, "
            f"timestamp={self.timestamp}>"
        )

    def __bool__(self) -> bool:
        return False
