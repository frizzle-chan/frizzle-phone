"""SDP answer generation and offer parsing for SIP."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class SdpOffer:
    """Parsed SDP offer with audio RTP endpoint info."""

    audio_port: int
    connection_address: str


def parse_sdp_offer(sdp: str) -> SdpOffer:
    """Extract audio port and connection address from an SDP offer."""
    audio_port = 0
    connection_address = "0.0.0.0"

    for line in sdp.splitlines():
        line = line.strip()
        if line.startswith("m=audio "):
            # m=audio <port> RTP/AVP ...
            parts = line.split()
            if len(parts) >= 2:
                audio_port = int(parts[1])
        elif line.startswith("c=IN IP4 "):
            # c=IN IP4 <address>[/<subnet>]
            addr = line[len("c=IN IP4 ") :]
            # Strip optional subnet mask
            connection_address = addr.split("/")[0].strip()

    return SdpOffer(audio_port=audio_port, connection_address=connection_address)


def build_sdp_answer(server_ip: str, rtp_port: int = 10000) -> str:
    """Build an SDP answer offering PCMU (payload type 0)."""
    lines = [
        "v=0",
        f"o=frizzle 0 0 IN IP4 {server_ip}",
        "s=frizzle-phone",
        f"c=IN IP4 {server_ip}",
        "t=0 0",
        f"m=audio {rtp_port} RTP/AVP 0",
        "a=rtpmap:0 PCMU/8000",
        "a=ptime:20",
        "",
    ]
    return "\r\n".join(lines)
