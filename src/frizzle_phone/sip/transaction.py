"""INVITE server transaction with Timer G/H/I (RFC 3261 §17.2.1 + RFC 6026)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import StrEnum

logger = logging.getLogger(__name__)

# RFC 3261 §17.1.1.1 default timer values
T1 = 0.5  # seconds — RTT estimate
T2 = 4.0  # seconds — max retransmit interval
T4 = 5.0  # seconds — max network round-trip time
TIMER_H_DURATION = 64 * T1  # 32 seconds — max wait for ACK


class TxnState(StrEnum):
    PROCEEDING = "proceeding"
    ACCEPTED = "accepted"
    CONFIRMED = "confirmed"
    TERMINATED = "terminated"


class InviteServerTxn:
    """RFC 6026 INVITE server transaction state machine.

    States: proceeding → accepted → confirmed → terminated

    - Timer G: retransmit 2xx (starts at T1, doubles to T2)
    - Timer H: max wait for ACK (32s) — fires on_timeout if ACK never arrives
    - Timer I: absorb ACK retransmissions (T4 = 5s)
    """

    def __init__(
        self,
        branch: str,
        transport: asyncio.DatagramTransport,
        loop: asyncio.AbstractEventLoop,
        on_timeout: Callable[[], None],
        on_terminated: Callable[[str], None],
    ) -> None:
        self.branch = branch
        self.state = TxnState.PROCEEDING
        self._last_response: bytes | None = None
        self._response_addr: tuple[str, int] | None = None
        self._transport = transport
        self._loop = loop
        self._on_timeout = on_timeout
        self._on_terminated = on_terminated
        self._timer_g: asyncio.TimerHandle | None = None
        self._timer_g_interval = T1
        self._timer_h: asyncio.TimerHandle | None = None
        self._timer_i: asyncio.TimerHandle | None = None

    def send_2xx(self, response: bytes, addr: tuple[str, int]) -> None:
        """Send 200 OK and enter Accepted state with retransmission timers."""
        self._last_response = response
        self._response_addr = addr
        self.state = TxnState.ACCEPTED
        self._transport.sendto(response, addr)
        # Timer G: retransmit 2xx at T1, doubling to T2
        self._timer_g_interval = T1
        self._timer_g = self._loop.call_later(self._timer_g_interval, self._fire_g)
        # Timer H: max wait for ACK
        self._timer_h = self._loop.call_later(TIMER_H_DURATION, self._fire_h)
        logger.debug(
            "INVITE txn %s: Accepted, Timer G=%.1fs H=%.0fs",
            self.branch,
            T1,
            TIMER_H_DURATION,
        )

    def receive_retransmit(self) -> None:
        """Retransmitted INVITE received — re-send last response."""
        if (
            self.state in (TxnState.ACCEPTED, TxnState.PROCEEDING)
            and self._last_response
            and self._response_addr
        ):
            logger.debug(
                "INVITE txn %s: retransmit detected, re-sending response", self.branch
            )
            self._transport.sendto(self._last_response, self._response_addr)

    def receive_ack(self) -> None:
        """ACK received — enter Confirmed state, absorb further ACKs."""
        if self.state != TxnState.ACCEPTED:
            return
        self.state = TxnState.CONFIRMED
        _cancel(self._timer_g)
        self._timer_g = None
        _cancel(self._timer_h)
        self._timer_h = None
        # Timer I: absorb ACK retransmissions for T4 seconds
        self._timer_i = self._loop.call_later(T4, self._fire_i)
        logger.debug("INVITE txn %s: Confirmed, Timer I=%.1fs", self.branch, T4)

    def terminate(self) -> None:
        """Externally terminate (e.g. CANCEL or BYE received)."""
        if self.state == TxnState.TERMINATED:
            return
        self._do_terminate()

    def _fire_g(self) -> None:
        """Timer G: retransmit 200 OK, double interval (cap at T2)."""
        if self.state != TxnState.ACCEPTED:
            return
        if self._last_response is not None and self._response_addr is not None:
            self._transport.sendto(self._last_response, self._response_addr)
        self._timer_g_interval = min(self._timer_g_interval * 2, T2)
        self._timer_g = self._loop.call_later(self._timer_g_interval, self._fire_g)
        logger.debug(
            "INVITE txn %s: Timer G retransmit, next=%.1fs",
            self.branch,
            self._timer_g_interval,
        )

    def _fire_h(self) -> None:
        """Timer H: ACK never arrived — transaction failed."""
        if self.state != TxnState.ACCEPTED:
            return
        logger.warning("INVITE txn %s: Timer H fired, ACK never received", self.branch)
        self._do_terminate()
        self._on_timeout()

    def _fire_i(self) -> None:
        """Timer I: done absorbing ACK retransmissions."""
        if self.state != TxnState.CONFIRMED:
            return
        logger.debug("INVITE txn %s: Timer I fired, cleaning up", self.branch)
        self._do_terminate()

    def _do_terminate(self) -> None:
        self.state = TxnState.TERMINATED
        _cancel(self._timer_g)
        _cancel(self._timer_h)
        _cancel(self._timer_i)
        self._timer_g = None
        self._timer_h = None
        self._timer_i = None
        self._on_terminated(self.branch)


def _cancel(handle: asyncio.TimerHandle | None) -> None:
    if handle is not None:
        handle.cancel()
