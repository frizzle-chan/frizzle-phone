"""Tests for SIP server request handling."""

import pytest

from frizzle_phone.sip.server import SipServer


def _make_invite(*, require: str | None = None, branch: str = "z9hG4bK001") -> bytes:
    """Build a minimal INVITE request."""
    lines = [
        "INVITE sip:frizzle@10.0.0.2 SIP/2.0",
        f"Via: SIP/2.0/UDP 10.0.0.1:5060;branch={branch}",
        "From: <sip:phone@10.0.0.1>;tag=abc",
        "To: <sip:frizzle@10.0.0.2>",
        "Call-ID: test-call@10.0.0.1",
        "CSeq: 1 INVITE",
    ]
    if require:
        lines.append(f"Require: {require}")
    lines += ["Content-Length: 0", "", ""]
    return "\r\n".join(lines).encode()


class FakeTransport:
    """Captures sendto() calls for test assertions."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        pass


def _make_server() -> tuple[SipServer, FakeTransport]:
    server = SipServer.__new__(SipServer)
    server._calls = {}
    server._invite_txns = {}
    server._server_ip = "10.0.0.2"
    server._audio_buf = b"\xff" * 160
    transport = FakeTransport()
    server._transport = transport
    return server, transport


ADDR = ("10.0.0.1", 5060)


def test_require_header_returns_420():
    """Require header with unsupported option triggers 420 Bad Extension."""
    server, transport = _make_server()
    server.datagram_received(_make_invite(require="100rel"), ADDR)
    # Should get a single 420 response (no 100 Trying, no 200 OK)
    assert len(transport.sent) == 1
    data, _addr = transport.sent[0]
    text = data.decode()
    assert "420 Bad Extension" in text
    assert "Unsupported: 100rel" in text


@pytest.mark.asyncio
async def test_no_require_header_proceeds_normally():
    """Without Require header, INVITE is processed normally."""
    server, transport = _make_server()
    server.datagram_received(_make_invite(), ADDR)
    # Should get 100 Trying + 200 OK
    assert len(transport.sent) >= 2
    responses = [d.decode() for d, _a in transport.sent]
    assert any("100 Trying" in r for r in responses)
    assert any("200 OK" in r for r in responses)
    # Clean up transactions
    for txn in list(server._invite_txns.values()):
        txn.terminate()
