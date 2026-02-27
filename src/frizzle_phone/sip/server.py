"""SIP UDP server with call state management."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import logging
import socket
from collections.abc import Callable

from frizzle_phone.rtp.stream import RtpStream
from frizzle_phone.sip.message import (
    SipMessage,
    build_request,
    build_response,
    extract_branch,
    generate_branch,
    generate_tag,
    parse_request,
)
from frizzle_phone.sip.sdp import build_sdp_answer, parse_sdp_offer
from frizzle_phone.sip.transaction import InviteServerTxn

logger = logging.getLogger(__name__)

_HandlerType = Callable[["SipMessage", tuple[str, int], tuple[str, int]], None]

ALLOWED_METHODS = (
    "INVITE, ACK, BYE, CANCEL, REGISTER, OPTIONS, REFER, SUBSCRIBE, NOTIFY"
)


def _add_via_received_params(msg: SipMessage, addr: tuple[str, int]) -> None:
    """Add received/rport Via params per RFC 3581.

    Mutates ``msg.headers`` in-place, tagging the top Via with the
    observed source IP and port.
    """
    for i, (key, value) in enumerate(msg.headers):
        if key.lower() == "via":
            msg.headers[i] = (key, f"{value};received={addr[0]};rport={addr[1]}")
            return


def _compute_response_addr(msg: SipMessage, addr: tuple[str, int]) -> tuple[str, int]:
    """Determine response address per RFC 3261 §18.2.2.

    Routes the response to the Via sent-by address (which Cisco phones set
    to their SIP listening port, typically 5060).
    """
    via = msg.header("Via")
    if via is None:
        return addr

    # Parse sent-by from Via: "SIP/2.0/UDP host:port;params"
    params = via.split(";")
    sent_by = params[0].strip()
    parts = sent_by.split(None, 1)
    if len(parts) < 2:
        return addr
    host_port = parts[1]

    host = addr[0]
    if ":" in host_port:
        port_str = host_port.rsplit(":", 1)[1]
        try:
            port = int(port_str)
        except ValueError:
            return addr
    else:
        port = 5060

    return (host, port)


@dataclasses.dataclass
class Call:
    call_id: str
    from_tag: str
    to_tag: str
    remote_addr: tuple[str, int]
    remote_contact: str
    remote_rtp_addr: tuple[str, int]
    rtp_stream: RtpStream | None = None
    invite_request: SipMessage | None = None
    invite_branch: str | None = None
    terminated: bool = False


def get_server_ip() -> str:
    """Detect the local IP address by opening a UDP socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


class SipServer(asyncio.DatagramProtocol):
    """SIP server handling REGISTER, INVITE, ACK, BYE, and CANCEL."""

    def __init__(
        self, *, server_ip: str, audio_buf: bytes, rtp_port: int = 10000
    ) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._calls: dict[str, Call] = {}
        self._invite_txns: dict[str, InviteServerTxn] = {}
        self._rtp_tasks: set[asyncio.Task[None]] = set()
        self._server_ip = server_ip
        self._audio_buf = audio_buf
        self._rtp_port = rtp_port
        self._handlers: dict[str, _HandlerType] = {
            "REGISTER": self._handle_register,
            "INVITE": self._handle_invite,
            "ACK": self._handle_ack,
            "BYE": self._handle_bye,
            "CANCEL": self._handle_cancel,
            "OPTIONS": self._handle_options,
            "REFER": self._handle_refer,
            "SUBSCRIBE": self._handle_subscribe,
            "NOTIFY": self._handle_notify,
        }

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]
        logger.info("SIP server listening")

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None:
            logger.warning("Connection lost: %s", exc)
        self._cleanup_all_calls()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # CRLF keepalive (RFC 5626 §4.4.1) — respond with CRLF
        stripped = data.strip(b"\r\n ")
        if not stripped:
            logger.debug("Keepalive CRLF from %s", addr)
            self._send(b"\r\n", addr)
            return

        logger.debug("Raw from %s:\n%s", addr, data.decode("utf-8", errors="replace"))
        try:
            msg = parse_request(data)
        except Exception:
            logger.exception("Failed to parse SIP message from %s", addr)
            return

        logger.info("Received %s from %s", msg.method, addr)

        # RFC 3581: tag Via with observed source address
        _add_via_received_params(msg, addr)
        # RFC 3261 §18.2.2: send responses to Via address, not packet source
        resp_addr = _compute_response_addr(msg, addr)

        # RFC 3261 §8.2.2: reject requests with unsupported Require options
        if msg.method not in ("ACK", "CANCEL"):
            require = msg.header("Require")
            if require:
                response = build_response(
                    msg,
                    420,
                    "Bad Extension",
                    extra_headers=[("Unsupported", require)],
                )
                self._send(response, resp_addr)
                return

        # Retransmission detection: if an INVITE matches an existing transaction,
        # re-send the cached response instead of re-processing (RFC 3261 §17.2.1)
        branch = extract_branch(msg)
        if branch and msg.method == "INVITE" and branch in self._invite_txns:
            self._invite_txns[branch].receive_retransmit()
            return

        handler = self._handlers.get(msg.method)

        if resp_addr != addr:
            logger.info(
                "Response to %s (Via sent-by) instead of %s (source)", resp_addr, addr
            )

        if handler is not None:
            handler(msg, addr, resp_addr)
        else:
            # Bug 11: respond 405 instead of silently ignoring
            response = build_response(
                msg,
                405,
                "Method Not Allowed",
                extra_headers=[("Allow", ALLOWED_METHODS)],
            )
            self._send(response, resp_addr)

    def _send(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._transport is not None:
            self._transport.sendto(data, addr)

    def _terminate_call(self, call: Call) -> None:
        """Mark a call as terminated and clean up its RTP stream and transaction."""
        call.terminated = True
        if call.rtp_stream is not None:
            call.rtp_stream.stop()
        if call.invite_branch:
            txn = self._invite_txns.pop(call.invite_branch, None)
            if txn is not None:
                txn.terminate()

    def _respond_200_ok(self, msg: SipMessage, resp_addr: tuple[str, int]) -> None:
        """Send a simple 200 OK response with a generated to-tag."""
        response = build_response(msg, 200, "OK", to_tag=generate_tag())
        self._send(response, resp_addr)

    def _handle_register(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        logger.debug("REGISTER from %s:\n%s", addr, msg.headers)
        contact = msg.header("Contact")
        extra: list[tuple[str, str]] = []
        if contact is not None:
            extra.append(("Contact", f"{contact};expires=3600"))
        # Expires header — use request value or default to 3600
        expires = msg.header("Expires") or "3600"
        extra.append(("Expires", expires))
        response = build_response(
            msg,
            200,
            "OK",
            to_tag=generate_tag(),
            extra_headers=extra,
        )
        logger.debug("REGISTER response:\n%s", response.decode())
        self._send(response, resp_addr)

    def _handle_invite(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        call_id = msg.header("Call-ID") or ""
        from_header = msg.header("From") or ""
        from_tag = ""
        if ";tag=" in from_header:
            from_tag = from_header.split(";tag=")[1].split(";")[0]

        # Parse SDP offer for remote RTP address (Bug 2)
        remote_rtp_addr = (addr[0], 0)
        if msg.body:
            offer = parse_sdp_offer(msg.body)
            remote_rtp_addr = (offer.connection_address, offer.audio_port)

        # Extract Contact header from INVITE (Bug 12)
        remote_contact = msg.header("Contact") or f"sip:{addr[0]}:{addr[1]}"

        # Generate to_tag once per dialog (Bug 1)
        to_tag = generate_tag()

        # Clean up existing call if re-INVITE (Bug 14)
        existing = self._calls.get(call_id)
        if existing is not None:
            self._terminate_call(existing)

        call = Call(
            call_id=call_id,
            from_tag=from_tag,
            to_tag=to_tag,
            remote_addr=addr,
            remote_contact=remote_contact,
            remote_rtp_addr=remote_rtp_addr,
            invite_request=msg,
        )
        self._calls[call_id] = call

        # 100 Trying — no to_tag (Bug 7)
        trying = build_response(msg, 100, "Trying")
        self._send(trying, resp_addr)

        # 200 OK with SDP, to_tag, and Contact (Bug 1, 6)
        sdp = build_sdp_answer(self._server_ip, self._rtp_port)
        ok = build_response(
            msg,
            200,
            "OK",
            body=sdp,
            to_tag=to_tag,
            extra_headers=[("Contact", f"<sip:frizzle@{self._server_ip}:5060>")],
        )

        # Send 200 OK through transaction layer for Timer G retransmission
        invite_branch = extract_branch(msg)
        if invite_branch and self._transport is not None:
            # Clean up any previous txn for this branch
            old_txn = self._invite_txns.pop(invite_branch, None)
            if old_txn is not None:
                old_txn.terminate()
            txn = InviteServerTxn(
                branch=invite_branch,
                transport=self._transport,
                loop=asyncio.get_running_loop(),
                on_timeout=lambda: asyncio.get_running_loop().call_soon(
                    self._send_bye, call
                ),
                on_terminated=self._remove_txn,
            )
            self._invite_txns[invite_branch] = txn
            call.invite_branch = invite_branch
            txn.send_2xx(ok, resp_addr)
        else:
            self._send(ok, resp_addr)

    def _handle_ack(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        call_id = msg.header("Call-ID") or ""
        call = self._calls.get(call_id)
        if call is None:
            logger.warning("ACK for unknown call: %s", call_id)
            return

        # Notify INVITE transaction that ACK arrived (stops Timer G)
        if call.invite_branch and call.invite_branch in self._invite_txns:
            self._invite_txns[call.invite_branch].receive_ack()

        self._start_rtp_for_call(call)

    def _handle_bye(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        call_id = msg.header("Call-ID") or ""
        call = self._calls.pop(call_id, None)
        if call is not None:
            self._terminate_call(call)
        tag = call.to_tag if call else generate_tag()
        response = build_response(msg, 200, "OK", to_tag=tag)
        self._send(response, resp_addr)

    def _handle_cancel(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        call_id = msg.header("Call-ID") or ""

        # Bug 8: look up call first, send 481 if not found
        call = self._calls.pop(call_id, None)
        if call is None:
            no_match = build_response(msg, 481, "Call/Transaction Does Not Exist")
            self._send(no_match, resp_addr)
            return

        # 200 OK for the CANCEL itself
        ok = build_response(msg, 200, "OK")
        self._send(ok, resp_addr)

        self._terminate_call(call)
        # 487 Request Terminated for the original INVITE
        if call.invite_request is not None:
            terminated = build_response(
                call.invite_request,
                487,
                "Request Terminated",
                to_tag=call.to_tag,
            )
            self._send(terminated, resp_addr)

    def _handle_options(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        """Respond to OPTIONS (used as keepalive by Cisco phones)."""
        response = build_response(
            msg,
            200,
            "OK",
            to_tag=generate_tag(),
            extra_headers=[("Allow", ALLOWED_METHODS)],
        )
        self._send(response, resp_addr)

    def _handle_refer(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        """Accept REFER (Cisco phones send alarm/diagnostic data via REFER)."""
        self._respond_200_ok(msg, resp_addr)

    def _handle_subscribe(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        """Accept SUBSCRIBE for MWI/presence."""
        self._respond_200_ok(msg, resp_addr)

    def _handle_notify(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        """Accept NOTIFY."""
        self._respond_200_ok(msg, resp_addr)

    def _start_rtp_for_call(self, call: Call) -> None:
        """Create an RTP stream for the call and schedule BYE on completion."""
        loop = asyncio.get_running_loop()
        done_future: asyncio.Future[None] = loop.create_future()
        call.rtp_stream = RtpStream(
            loop=loop,
            remote_addr=call.remote_rtp_addr,
            audio_buf=self._audio_buf,
            done_future=done_future,
            local_port=self._rtp_port,
        )
        done_future.add_done_callback(lambda _f: loop.call_soon(self._send_bye, call))
        task = loop.create_task(call.rtp_stream.start())
        self._rtp_tasks.add(task)
        task.add_done_callback(self._rtp_tasks.discard)

    def _send_bye(self, call: Call) -> None:
        """Send a BYE to the remote phone after audio finishes."""
        # Bug 15: check terminated flag to prevent double-BYE
        if call.terminated:
            return
        self._terminate_call(call)

        call_id = call.call_id
        remote_addr = call.remote_addr
        self._calls.pop(call_id, None)

        # Bug 12: use remote_contact as Request-URI, strip angle brackets
        request_uri = call.remote_contact.strip("<>")

        bye_msg = build_request(
            "BYE",
            request_uri,
            headers=[
                (
                    "Via",
                    f"SIP/2.0/UDP {self._server_ip}:5060;branch={generate_branch()}",
                ),
                # Bug 3: From uses our to_tag, To uses their from_tag
                ("From", f"<sip:frizzle@{self._server_ip}>;tag={call.to_tag}"),
                ("To", f"<sip:{remote_addr[0]}>;tag={call.from_tag}"),
                ("Call-ID", call_id),
                ("CSeq", "1 BYE"),
                ("Max-Forwards", "70"),
            ],
        )
        self._send(bye_msg, remote_addr)
        logger.info("Sent BYE for call %s", call_id)

    def _remove_txn(self, branch: str) -> None:
        """Callback for transaction cleanup after termination."""
        self._invite_txns.pop(branch, None)

    def _cleanup_all_calls(self) -> None:
        """Stop all active calls and transactions during shutdown (Bug 16)."""
        for txn in list(self._invite_txns.values()):
            txn.terminate()
        self._invite_txns.clear()
        for call in self._calls.values():
            call.terminated = True
            if call.rtp_stream is not None:
                call.rtp_stream.stop()
        self._calls.clear()
        for task in self._rtp_tasks:
            task.cancel()
        self._rtp_tasks.clear()


async def start_server(
    host: str = "0.0.0.0",
    port: int = 5060,
    *,
    server_ip: str,
    audio_buf: bytes,
    rtp_port: int = 10000,
) -> asyncio.DatagramTransport:
    loop = asyncio.get_running_loop()
    factory = functools.partial(
        SipServer, server_ip=server_ip, audio_buf=audio_buf, rtp_port=rtp_port
    )
    transport, _ = await loop.create_datagram_endpoint(factory, local_addr=(host, port))
    logger.info("Listening on %s:%d", host, port)
    return transport  # pyright: ignore[reportReturnType]
