"""Tests for INVITE server transaction (Timer G/H/I)."""

import asyncio
from collections.abc import Callable

import pytest

from frizzle_phone.sip.transaction import InviteServerTxn


class FakeTransport:
    """Captures sendto() calls."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))


ADDR = ("10.0.0.1", 5060)
RESPONSE = b"SIP/2.0 200 OK\r\n\r\n"
BRANCH = "z9hG4bKtest001"


def _noop_timeout() -> None:
    pass


def _noop_terminated(_branch: str) -> None:
    pass


def _make_txn(
    loop: asyncio.AbstractEventLoop,
    transport: FakeTransport | None = None,
    on_timeout: Callable[[], None] | None = None,
    on_terminated: Callable[[str], None] | None = None,
) -> tuple[InviteServerTxn, FakeTransport]:
    if transport is None:
        transport = FakeTransport()
    txn = InviteServerTxn(
        branch=BRANCH,
        transport=transport,  # type: ignore[arg-type]
        loop=loop,
        on_timeout=on_timeout if on_timeout is not None else _noop_timeout,
        on_terminated=(
            on_terminated if on_terminated is not None else _noop_terminated
        ),
    )
    return txn, transport


@pytest.mark.asyncio
async def test_send_2xx_sends_response():
    """send_2xx sends the response immediately."""
    loop = asyncio.get_running_loop()
    txn, transport = _make_txn(loop)
    txn.send_2xx(RESPONSE, ADDR)
    assert len(transport.sent) == 1
    assert transport.sent[0] == (RESPONSE, ADDR)
    assert txn.state == "accepted"
    txn.terminate()


@pytest.mark.asyncio
async def test_timer_g_retransmits():
    """Timer G retransmits 200 OK after T1 interval."""
    loop = asyncio.get_running_loop()
    txn, transport = _make_txn(loop)
    txn.send_2xx(RESPONSE, ADDR)
    assert len(transport.sent) == 1
    # Wait for Timer G to fire (T1 = 0.5s, use a bit more for margin)
    await asyncio.sleep(0.6)
    assert len(transport.sent) >= 2
    assert transport.sent[1] == (RESPONSE, ADDR)
    txn.terminate()


@pytest.mark.asyncio
async def test_timer_g_doubles():
    """Timer G interval doubles: T1=0.5 → 1.0 → 2.0 → 4.0 (capped at T2)."""
    loop = asyncio.get_running_loop()
    txn, transport = _make_txn(loop)
    txn.send_2xx(RESPONSE, ADDR)
    # After 0.5s: first retransmit. After 0.5+1.0=1.5s: second retransmit.
    await asyncio.sleep(1.6)
    # Initial send + at least 2 retransmits
    assert len(transport.sent) >= 3
    txn.terminate()


@pytest.mark.asyncio
async def test_ack_stops_retransmission():
    """receive_ack cancels Timer G — no more retransmissions."""
    loop = asyncio.get_running_loop()
    txn, transport = _make_txn(loop)
    txn.send_2xx(RESPONSE, ADDR)
    txn.receive_ack()
    assert txn.state == "confirmed"
    count_after_ack = len(transport.sent)
    # Wait past when Timer G would have fired
    await asyncio.sleep(0.7)
    assert len(transport.sent) == count_after_ack
    txn.terminate()


@pytest.mark.asyncio
async def test_timer_h_fires_on_timeout():
    """Timer H fires after 32s if ACK never arrives."""
    loop = asyncio.get_running_loop()
    timeout_called: list[bool] = []
    txn, _transport = _make_txn(
        loop,
        on_timeout=lambda: timeout_called.append(True),
    )
    # Monkey-patch a short Timer H for testing
    from frizzle_phone.sip import transaction

    original_h = transaction.TIMER_H_DURATION
    transaction.TIMER_H_DURATION = 0.1
    try:
        txn.send_2xx(RESPONSE, ADDR)
        # Cancel the existing timer and set a shorter one
        if txn._timer_h is not None:
            txn._timer_h.cancel()
        txn._timer_h = loop.call_later(0.1, txn._fire_h)
        await asyncio.sleep(0.2)
        assert timeout_called
        assert txn.state == "terminated"
    finally:
        transaction.TIMER_H_DURATION = original_h


@pytest.mark.asyncio
async def test_timer_i_terminates_after_ack():
    """Timer I terminates transaction T4 seconds after ACK."""
    loop = asyncio.get_running_loop()
    terminated: list[str] = []
    txn, _transport = _make_txn(
        loop,
        on_terminated=lambda b: terminated.append(b),
    )
    txn.send_2xx(RESPONSE, ADDR)
    txn.receive_ack()
    # Timer I = T4 = 5s — monkey-patch a short one for testing
    if txn._timer_i is not None:
        txn._timer_i.cancel()
    txn._timer_i = loop.call_later(0.1, txn._fire_i)
    await asyncio.sleep(0.2)
    assert txn.state == "terminated"
    assert BRANCH in terminated


@pytest.mark.asyncio
async def test_retransmitted_invite_resends_response():
    """receive_retransmit re-sends the cached 200 OK."""
    loop = asyncio.get_running_loop()
    txn, transport = _make_txn(loop)
    txn.send_2xx(RESPONSE, ADDR)
    assert len(transport.sent) == 1
    txn.receive_retransmit()
    assert len(transport.sent) == 2
    assert transport.sent[1] == (RESPONSE, ADDR)
    txn.terminate()


@pytest.mark.asyncio
async def test_receive_ack_idempotent():
    """Calling receive_ack multiple times (ACK retransmissions) is safe."""
    loop = asyncio.get_running_loop()
    txn, _transport = _make_txn(loop)
    txn.send_2xx(RESPONSE, ADDR)
    txn.receive_ack()
    assert txn.state == "confirmed"
    # Second ACK should be absorbed silently
    txn.receive_ack()
    assert txn.state == "confirmed"
    txn.terminate()


@pytest.mark.asyncio
async def test_terminate_is_idempotent():
    """Calling terminate multiple times does not crash."""
    loop = asyncio.get_running_loop()
    txn, _transport = _make_txn(loop)
    txn.send_2xx(RESPONSE, ADDR)
    txn.terminate()
    txn.terminate()  # should not raise
    assert txn.state == "terminated"
