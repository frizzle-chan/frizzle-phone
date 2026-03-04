"""Tests for SIP server request handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from frizzle_phone.bridge_manager import BridgeHandle
from frizzle_phone.sip.message import parse_message
from frizzle_phone.sip.server import (
    Call,
    DiscordBridgeContext,
    PendingBridge,
    SipServer,
)
from frizzle_phone.sip.transaction import TxnState

from .conftest import FakeTransport


async def _drain() -> None:
    """Yield enough event loop time for background DB tasks to complete."""
    await asyncio.sleep(0.05)


def _make_request(
    method: str,
    *,
    branch: str = "z9hG4bK001",
    call_id: str = "test-call@10.0.0.1",
    cseq: str | None = None,
    require: str | None = None,
    uri: str = "sip:frizzle@10.0.0.2",
) -> bytes:
    """Build a minimal SIP request."""
    if cseq is None:
        cseq = f"1 {method}"
    lines = [
        f"{method} {uri} SIP/2.0",
        f"Via: SIP/2.0/UDP 10.0.0.1:5060;branch={branch}",
        "From: <sip:phone@10.0.0.1>;tag=abc",
        "To: <sip:frizzle@10.0.0.2>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq}",
    ]
    if require:
        lines.append(f"Require: {require}")
    lines += ["Content-Length: 0", "", ""]
    return "\r\n".join(lines).encode()


def _make_invite(*, require: str | None = None, branch: str = "z9hG4bK001") -> bytes:
    """Build a minimal INVITE request."""
    return _make_request("INVITE", branch=branch, require=require)


def _make_server(db: aiosqlite.Connection) -> tuple[SipServer, FakeTransport]:
    server = SipServer(
        server_ip="10.0.0.2",
        db=db,
        audio_buffers={"techno": b"\xff" * 160},
        bot=MagicMock(),
    )
    transport = FakeTransport()
    server.connection_made(transport)
    return server, transport


ADDR = ("10.0.0.1", 5060)


def test_require_header_returns_420(db):
    """Require header with unsupported option triggers 420 Bad Extension."""
    server, transport = _make_server(db)
    server.datagram_received(_make_invite(require="100rel"), ADDR)
    # Should get a single 420 response (no 100 Trying, no 200 OK)
    assert len(transport.sent) == 1
    data, _addr = transport.sent[0]
    text = data.decode()
    assert "420 Bad Extension" in text
    assert "Unsupported: 100rel" in text


@pytest.mark.asyncio
async def test_no_require_header_proceeds_normally(seeded_db):
    """Without Require header, INVITE is processed normally."""
    server, transport = _make_server(seeded_db)
    server.datagram_received(_make_invite(), ADDR)
    await _drain()
    # Should get 100 Trying + 200 OK
    assert len(transport.sent) >= 2
    responses = [d.decode() for d, _a in transport.sent]
    assert any("100 Trying" in r for r in responses)
    assert any("200 OK" in r for r in responses)
    # Clean up transactions
    for txn in list(server._invite_txns.values()):
        txn.terminate()


@pytest.mark.asyncio
async def test_cancel_in_proceeding_sends_487_before_terminate(seeded_db):
    """CANCEL while INVITE txn is in PROCEEDING sends 200 + 487 and terminates."""
    server, transport = _make_server(seeded_db)
    call_id = "cancel-proceeding@test"

    # Send INVITE — creates call and txn (100 Trying + 200 OK)
    server.datagram_received(
        _make_request("INVITE", branch="z9hG4bKinv1", call_id=call_id), ADDR
    )
    await _drain()
    assert call_id in server._calls
    call = server._calls[call_id]

    # Force the txn back to PROCEEDING to simulate CANCEL arriving before 200
    if call.invite_branch:
        txn = server._invite_txns[call.invite_branch]
        txn.state = TxnState.PROCEEDING

    sent_before = len(transport.sent)

    # Send CANCEL
    server.datagram_received(
        _make_request("CANCEL", branch="z9hG4bKcan1", call_id=call_id), ADDR
    )

    # Should get 200 OK (to CANCEL) + 487 (to INVITE)
    cancel_responses = transport.sent[sent_before:]
    assert len(cancel_responses) == 2
    resp_200 = parse_message(cancel_responses[0][0])
    resp_487 = parse_message(cancel_responses[1][0])
    assert resp_200.uri == "200"
    assert resp_487.uri == "487"

    # Call should be removed and terminated
    assert call_id not in server._calls
    assert call.terminated


@pytest.mark.asyncio
async def test_unknown_extension_returns_404(db):
    """INVITE for an unregistered extension returns 404 Not Found."""
    server, transport = _make_server(db)
    server.datagram_received(_make_request("INVITE", uri="sip:999@10.0.0.2"), ADDR)
    await _drain()
    assert len(transport.sent) == 1
    data, _addr = transport.sent[0]
    text = data.decode()
    assert "404 Not Found" in text


@pytest.mark.asyncio
async def test_voice_disconnect_sends_bye(db):
    """Voice client disconnection triggers BYE to phone."""
    server, transport = _make_server(db)

    # Build a Call with an active discord bridge
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.stop = MagicMock()
    vc.disconnect = AsyncMock()

    handle = BridgeHandle(
        stop_event=asyncio.Event(),
        send_task=MagicMock(),
        rtp_transport=MagicMock(),
        voice_client=vc,
        sink=MagicMock(),
    )

    call = Call(
        call_id="dc-test@10.0.0.1",
        from_tag="abc",
        to_tag="xyz",
        remote_addr=ADDR,
        remote_contact=f"sip:phone@{ADDR[0]}:{ADDR[1]}",
        remote_from="<sip:phone@10.0.0.1>",
        remote_rtp_addr=("10.0.0.1", 20000),
    )
    call.discord_bridge = DiscordBridgeContext(
        voice_client=vc, guild_id=1, channel_id=2, handle=handle
    )
    server._calls[call.call_id] = call

    # Simulate: first poll connected, second poll disconnected
    vc.is_connected.side_effect = [True, False]

    with patch("frizzle_phone.sip.server.asyncio.sleep", new_callable=AsyncMock):
        await server._monitor_voice_connection(call)

    # Call should be terminated with BYE sent
    assert call.terminated
    bye_messages = [d.decode() for d, _a in transport.sent if b"BYE" in d]
    assert len(bye_messages) == 1


def _make_call(call_id: str = "test@10.0.0.1") -> Call:
    return Call(
        call_id=call_id,
        from_tag="abc",
        to_tag="xyz",
        remote_addr=ADDR,
        remote_contact=f"sip:phone@{ADDR[0]}:{ADDR[1]}",
        remote_from="<sip:phone@10.0.0.1>",
        remote_rtp_addr=("10.0.0.1", 20000),
    )


def test_get_bridged_calls_active_bridges(db):
    """get_bridged_calls returns (guild_id, channel_id) for active bridges."""
    server, _transport = _make_server(db)
    call = _make_call()
    call.discord_bridge = DiscordBridgeContext(
        voice_client=MagicMock(),
        guild_id=1,
        channel_id=2,
        handle=MagicMock(),
    )
    server._calls[call.call_id] = call

    assert server.get_bridged_calls() == [(1, 2)]


def test_get_bridged_calls_pending_bridges(db):
    """get_bridged_calls includes pending bridges."""
    server, _transport = _make_server(db)
    call = _make_call()
    call.pending_bridge = PendingBridge(
        voice_client=MagicMock(),
        guild_id=3,
        channel_id=4,
    )
    server._calls[call.call_id] = call

    assert server.get_bridged_calls() == [(3, 4)]


def test_get_bridged_calls_empty(db):
    """get_bridged_calls returns empty list when no bridges exist."""
    server, _transport = _make_server(db)
    assert server.get_bridged_calls() == []
