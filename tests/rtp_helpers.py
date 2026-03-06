"""Shared RTP test helpers."""

import asyncio


def parse_rtp_payload(data: bytes) -> bytes:
    """Extract payload from an RTP packet (skip fixed 12-byte header)."""
    return data[12:] if len(data) > 12 else b""


class RtpCollector(asyncio.DatagramProtocol):
    """Receives UDP datagrams into a list."""

    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.packets.append(data)
