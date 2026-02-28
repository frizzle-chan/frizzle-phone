"""End-to-end SIP server tests over real UDP sockets."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from frizzle_phone.sip.message import parse_message
from frizzle_phone.sip.server import start_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ClientProtocol(asyncio.DatagramProtocol):
    """Collects incoming datagrams in a queue."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait(data)


async def _recv(queue: asyncio.Queue[bytes], timeout: float = 2.0) -> bytes:
    """Receive one datagram, raising TimeoutError to avoid hangs."""
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def _recv_responses(
    queue: asyncio.Queue[bytes], n: int, timeout: float = 2.0
) -> list[bytes]:
    """Collect exactly *n* responses."""
    return [await _recv(queue, timeout=timeout) for _ in range(n)]


_SDP_BODY = (
    "v=0\r\n"
    "o=test 0 0 IN IP4 127.0.0.1\r\n"
    "s=test\r\n"
    "c=IN IP4 127.0.0.1\r\n"
    "t=0 0\r\n"
    "m=audio 0 RTP/AVP 0\r\n"
)


def _build_request(
    method: str,
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-test",
    branch: str = "z9hG4bKe2e",
    cseq: str = "1",
    body: str = "",
    extra_headers: list[str] | None = None,
    from_tag: str = "fromtag1",
) -> bytes:
    """Build a generic SIP request."""
    lines = [
        f"{method} sip:frizzle@127.0.0.1:{server_port} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch={branch}",
        f"From: <sip:test@127.0.0.1>;tag={from_tag}",
        "To: <sip:frizzle@127.0.0.1>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} {method}",
        f"Contact: <sip:test@127.0.0.1:{client_port}>",
        "Max-Forwards: 70",
    ]
    if extra_headers:
        lines.extend(extra_headers)
    body_bytes = body.encode() if body else b""
    if body:
        lines.append("Content-Type: application/sdp")
    lines.append(f"Content-Length: {len(body_bytes)}")
    lines += ["", ""]
    return "\r\n".join(lines).encode() + body_bytes


def _build_invite(
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-invite",
    branch: str = "z9hG4bKinv",
    extra_headers: list[str] | None = None,
) -> bytes:
    return _build_request(
        "INVITE",
        server_port,
        client_port,
        call_id=call_id,
        branch=branch,
        body=_SDP_BODY,
        extra_headers=extra_headers,
    )


def _build_ack(
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-invite",
    branch: str = "z9hG4bKack",
) -> bytes:
    return _build_request(
        "ACK",
        server_port,
        client_port,
        call_id=call_id,
        branch=branch,
    )


def _build_bye(
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-invite",
    branch: str = "z9hG4bKbye",
) -> bytes:
    return _build_request(
        "BYE",
        server_port,
        client_port,
        call_id=call_id,
        branch=branch,
        cseq="2",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sip_endpoint():
    """Start a SipServer on an OS-assigned port with a 1-packet audio buffer."""
    transport, server = await start_server(
        "127.0.0.1",
        0,
        server_ip="127.0.0.1",
        audio_buf=b"\x7f" * 160,
    )
    _, port = transport.get_extra_info("sockname")
    yield transport, server, port
    transport.close()


@pytest_asyncio.fixture
async def sip_client(sip_endpoint):
    """UDP client connected to the test SIP server."""
    transport, _server, server_port = sip_endpoint
    loop = asyncio.get_running_loop()
    proto = _ClientProtocol()
    client_transport, _ = await loop.create_datagram_endpoint(
        lambda: proto,
        remote_addr=("127.0.0.1", server_port),
    )
    client_port = client_transport.get_extra_info("sockname")[1]
    yield client_transport, proto.queue, server_port, client_port
    client_transport.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            "REGISTER",
            server_port,
            client_port,
            call_id="e2e-register",
            branch="z9hG4bKreg1",
        )
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "200"
    assert resp.header("Contact") is not None
    assert resp.header("Expires") is not None


@pytest.mark.asyncio
async def test_invite_100_and_200(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_invite(
            server_port,
            client_port,
            call_id="e2e-inv100",
            branch="z9hG4bKi100",
        )
    )
    responses = await _recv_responses(queue, 2)

    trying = parse_message(responses[0])
    assert trying.uri == "100"
    assert ";tag=" not in (trying.header("To") or "")

    ok = parse_message(responses[1])
    assert ok.uri == "200"
    assert ";tag=" in (ok.header("To") or "")
    assert ok.body and "m=audio" in ok.body
    assert ok.header("Contact") is not None


@pytest.mark.asyncio
async def test_full_call_invite_ack_bye(sip_client):
    transport, queue, server_port, client_port = sip_client
    cid = "e2e-full-call"

    transport.sendto(
        _build_invite(server_port, client_port, call_id=cid, branch="z9hG4bKfc1")
    )
    responses = await _recv_responses(queue, 2)
    assert parse_message(responses[0]).uri == "100"
    assert parse_message(responses[1]).uri == "200"

    transport.sendto(
        _build_ack(server_port, client_port, call_id=cid, branch="z9hG4bKfc2")
    )
    transport.sendto(
        _build_bye(server_port, client_port, call_id=cid, branch="z9hG4bKfc3")
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "200"


@pytest.mark.asyncio
async def test_invite_ack_auto_bye(sip_client):
    """Server auto-sends BYE when the 1-packet audio stream finishes."""
    transport, queue, server_port, client_port = sip_client
    cid = "e2e-auto-bye"

    transport.sendto(
        _build_invite(server_port, client_port, call_id=cid, branch="z9hG4bKab1")
    )
    await _recv_responses(queue, 2)  # 100 + 200

    transport.sendto(
        _build_ack(server_port, client_port, call_id=cid, branch="z9hG4bKab2")
    )
    data = await _recv(queue, timeout=3.0)
    assert data.startswith(b"BYE ")


@pytest.mark.asyncio
async def test_cancel_after_200_ok(sip_client):
    """CANCEL after 200 OK responds 200 but doesn't tear down (RFC 3261 §9.2)."""
    transport, queue, server_port, client_port = sip_client
    cid = "e2e-cancel-after-200"

    transport.sendto(
        _build_invite(server_port, client_port, call_id=cid, branch="z9hG4bKca1")
    )
    await _recv_responses(queue, 2)  # 100 + 200

    # CANCEL after 200 OK — should get 200 to CANCEL, but NOT 487
    transport.sendto(
        _build_request(
            "CANCEL",
            server_port,
            client_port,
            call_id=cid,
            branch="z9hG4bKca2",
        )
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "200"

    # Call is still alive — BYE returns 200 (not 481)
    transport.sendto(
        _build_bye(server_port, client_port, call_id=cid, branch="z9hG4bKca3")
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "200"


@pytest.mark.asyncio
async def test_cancel_unknown_call(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            "CANCEL",
            server_port,
            client_port,
            call_id="e2e-cancel-unknown",
            branch="z9hG4bKcu1",
        )
    )
    assert parse_message(await _recv(queue)).uri == "481"


@pytest.mark.asyncio
async def test_bye_unknown_call_returns_481(sip_client):
    """BYE for unknown Call-ID should return 481 (RFC 3261 §15.1.2)."""
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_bye(
            server_port, client_port, call_id="no-such-call", branch="z9hG4bKbu1"
        )
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "481"


@pytest.mark.asyncio
async def test_options(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            "OPTIONS",
            server_port,
            client_port,
            call_id="e2e-options",
            branch="z9hG4bKopt1",
        )
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "200"
    assert "INVITE" in (resp.header("Allow") or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["REFER", "SUBSCRIBE", "NOTIFY"])
async def test_stub_methods_return_200(sip_client, method):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            method,
            server_port,
            client_port,
            call_id=f"e2e-{method.lower()}",
            branch=f"z9hG4bK{method.lower()[:3]}1",
        )
    )
    assert parse_message(await _recv(queue)).uri == "200"


@pytest.mark.asyncio
async def test_unknown_method(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            "PUBLISH",
            server_port,
            client_port,
            call_id="e2e-publish",
            branch="z9hG4bKpub1",
        )
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "405"
    assert "INVITE" in (resp.header("Allow") or "")


@pytest.mark.asyncio
async def test_crlf_keepalive(sip_client):
    transport, queue, *_ = sip_client
    transport.sendto(b"\r\n\r\n")
    assert await _recv(queue) == b"\r\n"


@pytest.mark.asyncio
async def test_graceful_shutdown_sends_bye(sip_endpoint):
    """Graceful shutdown sends BYE to all active calls."""
    transport, server, server_port = sip_endpoint
    loop = asyncio.get_running_loop()
    proto = _ClientProtocol()
    client_transport, _ = await loop.create_datagram_endpoint(
        lambda: proto,
        remote_addr=("127.0.0.1", server_port),
    )
    client_port = client_transport.get_extra_info("sockname")[1]

    # Establish a call (INVITE → 100+200, no ACK so RTP doesn't auto-BYE)
    client_transport.sendto(
        _build_invite(
            server_port, client_port, call_id="e2e-shutdown", branch="z9hG4bKsd1"
        )
    )
    await _recv_responses(proto.queue, 2)  # 100 + 200

    server.graceful_shutdown()

    data = await _recv(proto.queue, timeout=2.0)
    assert data.startswith(b"BYE ")

    client_transport.close()


@pytest.mark.asyncio
async def test_duplicate_ack_ignored(sip_client):
    """Duplicate ACK retransmissions must not start a second RTP stream."""
    transport, queue, server_port, client_port = sip_client
    cid = "e2e-dup-ack"

    transport.sendto(
        _build_invite(server_port, client_port, call_id=cid, branch="z9hG4bKda1")
    )
    await _recv_responses(queue, 2)  # 100 + 200

    # First ACK — starts RTP, which will auto-send BYE when audio finishes
    transport.sendto(
        _build_ack(server_port, client_port, call_id=cid, branch="z9hG4bKda2")
    )
    # Duplicate ACK (retransmission) — must be absorbed
    transport.sendto(
        _build_ack(server_port, client_port, call_id=cid, branch="z9hG4bKda3")
    )

    # We should get exactly one BYE (from the auto-bye after audio finishes),
    # not an error-triggered BYE from a port conflict.
    data = await _recv(queue, timeout=3.0)
    assert data.startswith(b"BYE ")

    # No second BYE should arrive
    with pytest.raises(TimeoutError):
        await _recv(queue, timeout=0.5)


@pytest.mark.asyncio
async def test_require_unsupported(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_invite(
            server_port,
            client_port,
            call_id="e2e-require",
            branch="z9hG4bKreq1",
            extra_headers=["Require: 100rel"],
        )
    )
    resp = parse_message(await _recv(queue))
    assert resp.uri == "420"
    assert "100rel" in (resp.header("Unsupported") or "")
