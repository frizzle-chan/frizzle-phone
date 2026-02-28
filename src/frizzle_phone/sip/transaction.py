"""INVITE server transaction with Timer G/H/I.

RFC 3261 §17.2.1 defines the base INVITE server transaction state machine
(Proceeding → Completed → Confirmed → Terminated) for non-2xx responses.

RFC 6026 §7.1 revises §17.2.1 to add the "Accepted" state for 2xx responses,
moving 2xx retransmission responsibility into the transaction layer:
Proceeding → Accepted → Confirmed → Terminated.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import StrEnum

logger = logging.getLogger(__name__)

# RFC 3261 Appendix A, Table 4: default timer values
T1 = 0.5  # §17.1.1.1: RTT estimate (500ms default)
T2 = 4.0  # §17.1.2.2: max retransmit interval for INVITE responses
T4 = 5.0  # §17.1.2.2: max duration a message remains in the network
TIMER_H_DURATION = 64 * T1  # §17.2.1 Table 4: wait time for ACK receipt (32s)


class TxnState(StrEnum):
    """RFC 6026 §7.1 INVITE server transaction states (revises RFC 3261 Figure 7)."""

    PROCEEDING = "proceeding"  # RFC 3261 §17.2.1: initial state on INVITE receipt
    ACCEPTED = "accepted"  # RFC 6026 §7.1: 2xx sent, retransmitting until ACK
    CONFIRMED = "confirmed"  # RFC 3261 §17.2.1: ACK received, absorbing retransmits
    TERMINATED = "terminated"  # RFC 3261 §17.2.1: MUST be destroyed immediately


class InviteServerTxn:
    """RFC 6026 §7.1 INVITE server transaction state machine.

    States: Proceeding → Accepted → Confirmed → Terminated

    Timers (RFC 3261 §17.2.1, Table 4):
    - Timer G: retransmit 2xx, starts at T1, doubles up to T2
    - Timer H: 64*T1 (32s) max wait for ACK — fires on_timeout if ACK never arrives
    - Timer I: T4 (5s) absorb ACK retransmissions after Confirmed
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
        # RFC 3261 §17.2.1: server txn enters Proceeding on construction
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
        """Send 200 OK and enter Accepted state with retransmission timers.

        RFC 6026 §7.1: 2xx from TU transitions to Accepted (not Terminated
        as in original RFC 3261 §17.2.1) so the txn layer retransmits.
        """
        self._last_response = response
        self._response_addr = addr
        # RFC 6026 §7.1: transition Proceeding → Accepted
        self.state = TxnState.ACCEPTED
        # RFC 3261 §17.2.1: MUST pass 2xx to transport for transmission
        self._transport.sendto(response, addr)
        # RFC 3261 §17.2.1: Timer G initially T1 for unreliable transports;
        # fires to retransmit response, interval doubles up to T2
        self._timer_g_interval = T1
        self._timer_g = self._loop.call_later(self._timer_g_interval, self._fire_g)
        # RFC 3261 §17.2.1: Timer H MUST be set to 64*T1; determines when
        # the server txn abandons retransmitting the response
        self._timer_h = self._loop.call_later(TIMER_H_DURATION, self._fire_h)
        logger.debug(
            "INVITE txn %s: Accepted, Timer G=%.1fs H=%.0fs",
            self.branch,
            T1,
            TIMER_H_DURATION,
        )

    def receive_retransmit(self) -> None:
        """Retransmitted INVITE received -- re-send last response.

        RFC 3261 §17.2.1: in Proceeding, MUST retransmit most recent
        provisional response; in Completed/Accepted, SHOULD retransmit
        the final response.
        """
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
        """ACK received -- enter Confirmed state, absorb further ACKs.

        RFC 3261 §17.2.1: ACK in Accepted/Completed MUST transition to
        Confirmed. Timer G is ignored (retransmissions cease).
        """
        if self.state != TxnState.ACCEPTED:
            return
        # RFC 3261 §17.2.1: MUST transition to Confirmed
        self.state = TxnState.CONFIRMED
        # Cancel G and H: retransmissions and ACK-wait are no longer needed
        _cancel(self._timer_g)
        self._timer_g = None
        _cancel(self._timer_h)
        self._timer_h = None
        # RFC 3261 §17.2.1: Timer I set to T4 for unreliable transports;
        # absorbs ACK retransmissions until MUST transition to Terminated
        self._timer_i = self._loop.call_later(T4, self._fire_i)
        logger.debug("INVITE txn %s: Confirmed, Timer I=%.1fs", self.branch, T4)

    def terminate(self) -> None:
        """Externally terminate (e.g. CANCEL or BYE received)."""
        if self.state == TxnState.TERMINATED:
            return
        self._do_terminate()

    def _fire_g(self) -> None:
        """Timer G fired: retransmit response and reschedule.

        RFC 3261 §17.2.1: when Timer G fires, response is passed to
        transport for retransmission; G is reset to min(2*previous, T2).
        """
        if self.state != TxnState.ACCEPTED:
            return
        if self._last_response is not None and self._response_addr is not None:
            self._transport.sendto(self._last_response, self._response_addr)
        # RFC 3261 §17.2.1: double interval, cap at T2
        self._timer_g_interval = min(self._timer_g_interval * 2, T2)
        self._timer_g = self._loop.call_later(self._timer_g_interval, self._fire_g)
        logger.debug(
            "INVITE txn %s: Timer G retransmit, next=%.1fs",
            self.branch,
            self._timer_g_interval,
        )

    def _fire_h(self) -> None:
        """Timer H fired: ACK never arrived, transaction failed.

        RFC 3261 §17.2.1: if Timer H fires in Completed/Accepted, the
        server txn MUST transition to Terminated and MUST indicate a
        transaction failure to the TU.
        """
        if self.state != TxnState.ACCEPTED:
            return
        logger.warning("INVITE txn %s: Timer H fired, ACK never received", self.branch)
        self._do_terminate()
        self._on_timeout()

    def _fire_i(self) -> None:
        """Timer I fired: ACK absorption period over.

        RFC 3261 §17.2.1: once Timer I fires the server MUST transition
        to Terminated. The Confirmed state existed solely to absorb
        retransmitted ACKs.
        """
        if self.state != TxnState.CONFIRMED:
            return
        logger.debug("INVITE txn %s: Timer I fired, cleaning up", self.branch)
        self._do_terminate()

    def _do_terminate(self) -> None:
        # RFC 3261 §17.2.1: in the Terminated state, the transaction
        # MUST be destroyed immediately
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
