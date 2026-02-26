"""SIP message parser and response builder."""

from __future__ import annotations

import dataclasses


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

    def header_values(self, name: str) -> list[str]:
        """Return all values for a header name (case-insensitive)."""
        lower = name.lower()
        return [value for key, value in self.headers if key.lower() == lower]


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
            headers.append((key.strip(), value.strip()))

    return SipMessage(
        method=method,
        uri=uri,
        version=version,
        headers=headers,
        body=body,
    )


def build_response(
    request: SipMessage,
    status_code: int,
    reason: str,
    body: str = "",
    *,
    to_tag: str | None = None,
    extra_headers: list[tuple[str, str]] | None = None,
) -> bytes:
    """Build a SIP response mirroring key headers from the request."""
    lines = [f"SIP/2.0 {status_code} {reason}"]

    # Mirror ALL Via headers in order (Bug 5)
    for key, value in request.headers:
        if key.lower() == "via":
            lines.append(f"Via: {value}")

    # Mirror From, Call-ID, CSeq
    for hdr in ("From", "Call-ID", "CSeq"):
        value = request.header(hdr)
        if value is not None:
            lines.append(f"{hdr}: {value}")

    # To header — only add tag when explicitly provided (Bug 1 + 7)
    to_value = request.header("To")
    if to_value is not None:
        if to_tag is not None and ";tag=" not in to_value:
            to_value = f"{to_value};tag={to_tag}"
        lines.append(f"To: {to_value}")

    # Extra headers (e.g. Contact, Allow)
    if extra_headers:
        for hdr_name, hdr_value in extra_headers:
            lines.append(f"{hdr_name}: {hdr_value}")

    # Content-Length uses byte count (Bug 4)
    body_bytes = body.encode("utf-8") if body else b""
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body_bytes)}")

    lines.append("")
    response_bytes = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    if body_bytes:
        response_bytes += body_bytes

    return response_bytes
