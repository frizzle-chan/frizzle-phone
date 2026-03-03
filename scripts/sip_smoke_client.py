#!/usr/bin/env python3
"""Minimal SIP smoke-test client (stdlib only, runs on CI host)."""

import random
import socket
import string
import sys
import time

SERVER = "127.0.0.1"
TIMEOUT = 5.0


def _tag() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _branch() -> str:
    chars = string.ascii_lowercase + string.digits
    return "z9hG4bK" + "".join(random.choices(chars, k=8))


def _call_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _build_invite(
    extension: str, sip_port: int, local_port: int
) -> tuple[bytes, str, str, str]:
    """Build an INVITE request, return (msg_bytes, call_id, from_tag, branch)."""
    call_id = _call_id()
    from_tag = _tag()
    branch = _branch()

    sdp = (
        "v=0\r\n"
        f"o=smoke 0 0 IN IP4 {SERVER}\r\n"
        "s=smoke-test\r\n"
        f"c=IN IP4 {SERVER}\r\n"
        "t=0 0\r\n"
        f"m=audio {local_port} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
    )
    sdp_bytes = sdp.encode()

    lines = [
        f"INVITE sip:{extension}@{SERVER}:{sip_port} SIP/2.0",
        f"Via: SIP/2.0/UDP {SERVER}:{local_port};branch={branch};rport",
        f"From: <sip:smoke@{SERVER}>;tag={from_tag}",
        f"To: <sip:{extension}@{SERVER}:{sip_port}>",
        f"Call-ID: {call_id}",
        "CSeq: 1 INVITE",
        "Max-Forwards: 70",
        f"Contact: <sip:smoke@{SERVER}:{local_port}>",
        "Content-Type: application/sdp",
        f"Content-Length: {len(sdp_bytes)}",
        "",
        "",
    ]
    msg = "\r\n".join(lines).encode() + sdp_bytes
    return msg, call_id, from_tag, branch


def _build_ack(
    extension: str,
    sip_port: int,
    local_port: int,
    call_id: str,
    from_tag: str,
    to_tag: str,
) -> bytes:
    branch = _branch()
    lines = [
        f"ACK sip:{extension}@{SERVER}:{sip_port} SIP/2.0",
        f"Via: SIP/2.0/UDP {SERVER}:{local_port};branch={branch};rport",
        f"From: <sip:smoke@{SERVER}>;tag={from_tag}",
        f"To: <sip:{extension}@{SERVER}:{sip_port}>;tag={to_tag}",
        f"Call-ID: {call_id}",
        "CSeq: 1 ACK",
        "Max-Forwards: 70",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


def _recv_responses(sock: socket.socket, deadline: float) -> list[str]:
    """Receive SIP responses until deadline, return list of raw decoded messages."""
    responses: list[str] = []
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(max(remaining, 0.1))
        try:
            data = sock.recv(4096)
            responses.append(data.decode("utf-8", errors="replace"))
        except TimeoutError:
            break
    return responses


def _extract_status(response: str) -> int:
    """Extract status code from SIP response first line."""
    first_line = response.split("\r\n", 1)[0]
    parts = first_line.split(" ", 2)
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 0


def _extract_to_tag(response: str) -> str:
    """Extract the To tag from a SIP response."""
    for line in response.split("\r\n"):
        if line.lower().startswith("to:"):
            value = line.split(":", 1)[1]
            if ";tag=" in value:
                return value.split(";tag=")[1].split(";")[0].strip()
    return ""


def _is_request(msg: str) -> bool:
    """Check if a SIP message is a request (vs response)."""
    first_line = msg.split("\r\n", 1)[0]
    return not first_line.startswith("SIP/")


passed = 0
failed = 0


def check_pass(name: str) -> None:
    global passed
    print(f"  PASS: {name}")
    passed += 1


def check_fail(name: str) -> None:
    global failed
    print(f"  FAIL: {name}")
    failed += 1


def test_unknown_extension(sip_port: int, sock: socket.socket, local_port: int) -> None:
    """INVITE sip:999 → expect 404 Not Found."""
    invite, call_id, from_tag, branch = _build_invite("999", sip_port, local_port)
    sock.sendto(invite, (SERVER, sip_port))

    deadline = time.monotonic() + TIMEOUT
    responses = _recv_responses(sock, deadline)

    got_404 = any(_extract_status(r) == 404 for r in responses if not _is_request(r))
    if got_404:
        check_pass("INVITE sip:999 → 404 Not Found")
    else:
        statuses = [_extract_status(r) for r in responses if not _is_request(r)]
        check_fail(f"INVITE sip:999 → expected 404, got {statuses}")


def test_audio_extension(sip_port: int, sock: socket.socket, local_port: int) -> None:
    """INVITE sip:200 (audio) → expect 100 + 200, send ACK, expect BYE."""
    invite, call_id, from_tag, branch = _build_invite("200", sip_port, local_port)
    sock.sendto(invite, (SERVER, sip_port))

    # Collect responses — need 100 Trying + 200 OK
    deadline = time.monotonic() + TIMEOUT
    messages: list[str] = []
    got_100 = False
    got_200 = False
    to_tag = ""

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(max(remaining, 0.1))
        try:
            data = sock.recv(4096)
            msg = data.decode("utf-8", errors="replace")
            messages.append(msg)
            if not _is_request(msg):
                status = _extract_status(msg)
                if status == 100:
                    got_100 = True
                elif status == 200:
                    got_200 = True
                    to_tag = _extract_to_tag(msg)
                    break
        except TimeoutError:
            break

    if got_100:
        check_pass("INVITE sip:200 → 100 Trying")
    else:
        check_fail("INVITE sip:200 → missing 100 Trying")

    if got_200:
        check_pass("INVITE sip:200 → 200 OK")
    else:
        statuses = [_extract_status(m) for m in messages if not _is_request(m)]
        check_fail(f"INVITE sip:200 → expected 200, got {statuses}")
        return

    # Send ACK
    ack = _build_ack("200", sip_port, local_port, call_id, from_tag, to_tag)
    sock.sendto(ack, (SERVER, sip_port))

    # Wait for BYE (server sends BYE after audio playback finishes)
    # Audio is ~60s of techno, but the server sends it as fast as RTP timing allows.
    # Give it a generous timeout.
    bye_deadline = time.monotonic() + 120
    got_bye = False

    while time.monotonic() < bye_deadline:
        remaining = bye_deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(max(remaining, 0.1))
        try:
            data = sock.recv(4096)
            msg = data.decode("utf-8", errors="replace")
            if _is_request(msg) and msg.startswith("BYE "):
                got_bye = True
                break
            # Handle 200 OK retransmissions — re-send ACK
            if not _is_request(msg) and _extract_status(msg) == 200:
                sock.sendto(ack, (SERVER, sip_port))
        except TimeoutError:
            break

    if got_bye:
        check_pass("Server sent BYE after audio playback")
    else:
        check_fail("Server did not send BYE within 120s")


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <sip_port>", file=sys.stderr)
        return 1

    sip_port = int(sys.argv[1])

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((SERVER, 0))
    local_port = sock.getsockname()[1]

    print(f"SIP smoke client → {SERVER}:{sip_port} (local port {local_port})")

    test_unknown_extension(sip_port, sock, local_port)
    test_audio_extension(sip_port, sock, local_port)

    sock.close()

    if failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
