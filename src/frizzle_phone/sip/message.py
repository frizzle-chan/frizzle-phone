"""SIP message parser and response builder."""

from __future__ import annotations

import dataclasses
import random
import string

# RFC 3261 §7.3.1 — compact header form abbreviations
_COMPACT_HEADERS = {
    "v": "Via",
    "f": "From",
    "t": "To",
    "i": "Call-ID",
    "m": "Contact",
    "l": "Content-Length",
    "c": "Content-Type",
}


@dataclasses.dataclass
class SipMessage:
    """Parsed SIP request."""

    method: str
    uri: str
    version: str
    headers: list[tuple[str, str]]
    body: str

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup (returns first match)."""
        lower = name.lower()
        for key, value in self.headers:
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

    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            key = _COMPACT_HEADERS.get(key, key)
            headers.append((key, value.strip()))

    return SipMessage(
        method=method,
        uri=uri,
        version=version,
        headers=headers,
        body=body,
    )


def _encode_message(lines: list[str], body: str, content_type: str) -> bytes:
    """Encode header lines + body into a complete SIP message."""
    body_bytes = body.encode("utf-8") if body else b""
    if body_bytes:
        lines.append(f"Content-Type: {content_type}")
    lines.append(f"Content-Length: {len(body_bytes)}")
    lines.append("")
    msg_bytes = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    if body_bytes:
        msg_bytes += body_bytes
    return msg_bytes


def build_response(
    request: SipMessage,
    status_code: int,
    reason: str,
    body: str = "",
    *,
    to_tag: str | None = None,
    extra_headers: list[tuple[str, str]] | None = None,
    content_type: str = "application/sdp",
) -> bytes:
    """Build a SIP response mirroring key headers from the request."""
    lines = [f"SIP/2.0 {status_code} {reason}"]

    # Mirror all Via headers in order (RFC 3261 §8.2.6.2)
    for key, value in request.headers:
        if key.lower() == "via":
            lines.append(f"Via: {value}")

    # Mirror From, Call-ID, CSeq
    for hdr in ("From", "Call-ID", "CSeq"):
        value = request.header(hdr)
        if value is not None:
            lines.append(f"{hdr}: {value}")

    # To header — only add tag when explicitly provided
    to_value = request.header("To")
    if to_value is not None:
        if to_tag is not None and ";tag=" not in to_value:
            to_value = f"{to_value};tag={to_tag}"
        lines.append(f"To: {to_value}")

    # Extra headers (e.g. Contact, Allow)
    if extra_headers:
        for hdr_name, hdr_value in extra_headers:
            lines.append(f"{hdr_name}: {hdr_value}")

    return _encode_message(lines, body, content_type)


def build_request(
    method: str,
    uri: str,
    *,
    headers: list[tuple[str, str]],
    body: str = "",
    content_type: str = "application/sdp",
) -> bytes:
    """Build a SIP request message.

    Parameters:
        method: SIP method (e.g. "BYE", "INVITE")
        uri: Request-URI
        headers: List of (name, value) header tuples
        body: Optional message body
        content_type: Content-Type when body is present
    """
    lines = [f"{method} {uri} SIP/2.0"]
    for name, value in headers:
        lines.append(f"{name}: {value}")
    return _encode_message(lines, body, content_type)


# ---------------------------------------------------------------------------
# SIP header/parameter utilities
# ---------------------------------------------------------------------------

_TAG_CHARS = string.ascii_lowercase + string.digits


def generate_tag() -> str:
    """Generate a random SIP tag value."""
    return "".join(random.choices(_TAG_CHARS, k=8))


def generate_branch() -> str:
    """Generate a random Via branch parameter (RFC 3261 magic cookie prefix)."""
    return "z9hG4bK" + "".join(random.choices(_TAG_CHARS, k=8))


def extract_branch(msg: SipMessage) -> str | None:
    """Extract the Via branch parameter for transaction matching."""
    via = msg.header("Via")
    if via is None:
        return None
    for param in via.split(";"):
        param = param.strip()
        if param.startswith("branch="):
            return param[7:]
    return None
