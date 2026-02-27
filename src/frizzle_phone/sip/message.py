"""SIP message parser and response builder."""

from __future__ import annotations

import dataclasses
import random
import string

# RFC 3261 §7.3.3: compact header forms — implementations MUST accept
# both long and short forms of each header name (§20 defines the mappings)
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
        """Case-insensitive header lookup (returns first match).

        RFC 3261 §7.3.1: header field names are always case-insensitive.
        """
        lower = name.lower()
        for key, value in self.headers:
            if key.lower() == lower:
                return value
        return None


def parse_message(data: bytes) -> SipMessage:
    """Parse a SIP message (request or response) from raw bytes."""
    # RFC 3261 §7: SIP is UTF-8 text; messages use CRLF line endings
    text = data.decode("utf-8", errors="replace")
    # RFC 3261 §7: empty line (CRLF CRLF) separates headers from body
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")

    # RFC 3261 §7.1: Request-Line = Method SP Request-URI SP SIP-Version CRLF
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
            # RFC 3261 §7.3.3: expand compact header forms to canonical names
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
    # RFC 3261 §7.4.1: Content-Type MUST indicate the media type of the body
    if body_bytes:
        lines.append(f"Content-Type: {content_type}")
    # RFC 3261 §7.4.2: Content-Length provides the body length in bytes
    lines.append(f"Content-Length: {len(body_bytes)}")
    # RFC 3261 §7: each line MUST be terminated by CRLF; the empty line
    # separating headers from body MUST be present even if body is empty
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
    # RFC 3261 §7.2: Status-Line = SIP-Version SP Status-Code SP Reason-Phrase
    lines = [f"SIP/2.0 {status_code} {reason}"]

    # RFC 3261 §8.2.6.2: Via header field values in the response MUST equal
    # those in the request and MUST maintain the same ordering
    for key, value in request.headers:
        if key.lower() == "via":
            lines.append(f"Via: {value}")

    # RFC 3261 §8.2.6.2: From, Call-ID, and CSeq in response MUST equal
    # the corresponding fields from the request
    for hdr in ("From", "Call-ID", "CSeq"):
        value = request.header(hdr)
        if value is not None:
            lines.append(f"{hdr}: {value}")

    # RFC 3261 §8.2.6.2: if the request To has no tag, the UAS MUST add one
    # (except 100 Trying, where a tag SHOULD NOT be added per §17.2.1);
    # if a tag was already present, the To header MUST be echoed unchanged
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
    # RFC 3261 §7.1: Request-Line = Method SP Request-URI SP SIP-Version CRLF
    lines = [f"{method} {uri} SIP/2.0"]
    for name, value in headers:
        lines.append(f"{name}: {value}")
    return _encode_message(lines, body, content_type)


# ---------------------------------------------------------------------------
# SIP header/parameter utilities
# ---------------------------------------------------------------------------

_TAG_CHARS = string.ascii_lowercase + string.digits


def generate_tag() -> str:
    """Generate a random SIP tag value.

    RFC 3261 §19.3: tags MUST be globally unique and cryptographically random
    with at least 32 bits of randomness.
    """
    return "".join(random.choices(_TAG_CHARS, k=8))


def generate_branch() -> str:
    """Generate a random Via branch parameter.

    RFC 3261 §8.1.1.7: the branch parameter MUST be unique across space and
    time for all requests.  It MUST begin with the magic cookie "z9hG4bK" so
    receivers can identify RFC 3261-compliant transaction IDs (§17.1.3).
    """
    return "z9hG4bK" + "".join(random.choices(_TAG_CHARS, k=8))


def parse_via_params(via: str) -> dict[str, str]:
    """Parse Via header semicolon-delimited parameters into a dict.

    RFC 3261 §20.42: a Via value contains the transport protocol and sent-by
    address followed by semicolon-delimited parameters (branch, received,
    rport, maddr, ttl, etc.).

    Parameters without values (e.g. bare ``rport``) get empty string values.
    The first segment (protocol/sent-by) is excluded.
    """
    params: dict[str, str] = {}
    for part in via.split(";")[1:]:
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip()] = value.strip()
        else:
            params[part] = ""
    return params


def extract_branch(msg: SipMessage) -> str | None:
    """Extract the Via branch parameter for transaction matching.

    RFC 3261 §17.1.3: the branch in the topmost Via identifies the client
    transaction; a response is matched to its transaction by comparing this
    value along with the CSeq method.
    """
    via = msg.header("Via")
    if via is None:
        return None
    return parse_via_params(via).get("branch")
