"""SDP answer generation for SIP INVITE responses."""


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
