"""SIP message parser and response builder."""

from __future__ import annotations

import dataclasses
import random
import string


@dataclasses.dataclass
class SipMessage:
    """Parsed SIP request."""

    method: str
    uri: str
    version: str
    headers: dict[str, str]
    body: str

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup."""
        lower = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lower:
                return value
        return None


def parse_request(data: bytes) -> SipMessage:
    """Parse a SIP request from raw bytes."""
    text = data.decode("utf-8", errors="replace")
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")

    request_line = lines[0]
    parts = request_line.split(" ", 2)
    method = parts[0]
    uri = parts[1] if len(parts) > 1 else ""
    version = parts[2] if len(parts) > 2 else "SIP/2.0"

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip()] = value.strip()

    return SipMessage(
        method=method,
        uri=uri,
        version=version,
        headers=headers,
        body=body,
    )


def _generate_tag() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def build_response(
    request: SipMessage,
    status_code: int,
    reason: str,
    body: str = "",
) -> bytes:
    """Build a SIP response mirroring key headers from the request."""
    lines = [f"SIP/2.0 {status_code} {reason}"]

    # Mirror required headers
    for hdr in ("Via", "From", "Call-ID", "CSeq"):
        value = request.header(hdr)
        if value is not None:
            lines.append(f"{hdr}: {value}")

    # To header — add tag if not present
    to_value = request.header("To")
    if to_value is not None:
        if ";tag=" not in to_value:
            to_value = f"{to_value};tag={_generate_tag()}"
        lines.append(f"To: {to_value}")

    if body:
        lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body)}")
    else:
        lines.append("Content-Length: 0")

    lines.append("")
    response = "\r\n".join(lines) + "\r\n"
    if body:
        response += body

    return response.encode("utf-8")
