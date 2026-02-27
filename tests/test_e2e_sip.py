"""End-to-end SIP server tests over real UDP sockets."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from frizzle_phone.sip.message import parse_request
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
    transport = await start_server(
        "127.0.0.1",
        0,
        server_ip="127.0.0.1",
        audio_buf=b"\x7f" * 160,
        rtp_port=0,
    )
    _, port = transport.get_extra_info("sockname")
    yield transport, port
    transport.close()


@pytest_asyncio.fixture
async def sip_client(sip_endpoint):
    """UDP client connected to the test SIP server."""
    transport, server_port = sip_endpoint
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
    resp = parse_request(await _recv(queue))
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

    trying = parse_request(responses[0])
    assert trying.uri == "100"
    assert ";tag=" not in (trying.header("To") or "")

    ok = parse_request(responses[1])
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
    assert parse_request(responses[0]).uri == "100"
    assert parse_request(responses[1]).uri == "200"

    transport.sendto(
        _build_ack(server_port, client_port, call_id=cid, branch="z9hG4bKfc2")
    )
    transport.sendto(
        _build_bye(server_port, client_port, call_id=cid, branch="z9hG4bKfc3")
    )
    resp = parse_request(await _recv(queue))
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
async def test_cancel_active_invite(sip_client):
    transport, queue, server_port, client_port = sip_client
    cid = "e2e-cancel"

    transport.sendto(
        _build_invite(server_port, client_port, call_id=cid, branch="z9hG4bKca1")
    )
    await _recv_responses(queue, 2)  # 100 + 200

    transport.sendto(
        _build_request(
            "CANCEL",
            server_port,
            client_port,
            call_id=cid,
            branch="z9hG4bKca2",
        )
    )
    responses = await _recv_responses(queue, 2)
    codes = {parse_request(r).uri for r in responses}
    assert "200" in codes
    assert "487" in codes


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
    assert parse_request(await _recv(queue)).uri == "481"


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
    resp = parse_request(await _recv(queue))
    assert resp.uri == "200"
    assert "INVITE" in (resp.header("Allow") or "")


@pytest.mark.asyncio
async def test_refer(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            "REFER",
            server_port,
            client_port,
            call_id="e2e-refer",
            branch="z9hG4bKref1",
        )
    )
    assert parse_request(await _recv(queue)).uri == "200"


@pytest.mark.asyncio
async def test_subscribe(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            "SUBSCRIBE",
            server_port,
            client_port,
            call_id="e2e-subscribe",
            branch="z9hG4bKsub1",
        )
    )
    assert parse_request(await _recv(queue)).uri == "200"


@pytest.mark.asyncio
async def test_notify(sip_client):
    transport, queue, server_port, client_port = sip_client
    transport.sendto(
        _build_request(
            "NOTIFY",
            server_port,
            client_port,
            call_id="e2e-notify",
            branch="z9hG4bKnot1",
        )
    )
    assert parse_request(await _recv(queue)).uri == "200"


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
    resp = parse_request(await _recv(queue))
    assert resp.uri == "405"
    assert "INVITE" in (resp.header("Allow") or "")


@pytest.mark.asyncio
async def test_crlf_keepalive(sip_client):
    transport, queue, *_ = sip_client
    transport.sendto(b"\r\n\r\n")
    assert await _recv(queue) == b"\r\n"


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
    resp = parse_request(await _recv(queue))
    assert resp.uri == "420"
    assert "100rel" in (resp.header("Unsupported") or "")
