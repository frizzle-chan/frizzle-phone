"""SIP call state dataclass."""

from __future__ import annotations

import dataclasses

from frizzle_phone.rtp.stream import RtpStream
from frizzle_phone.sip.message import SipMessage


@dataclasses.dataclass
class Call:
    call_id: str
    from_tag: str
    to_tag: str
    remote_addr: tuple[str, int]
    remote_contact: str
    remote_rtp_addr: tuple[str, int]
    rtp_stream: RtpStream | None = None
    invite_request: SipMessage | None = None
    invite_branch: str | None = None
    terminated: bool = False
