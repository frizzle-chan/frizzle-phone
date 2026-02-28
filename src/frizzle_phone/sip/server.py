"""SIP UDP server with call state management."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import socket
from collections.abc import Callable

from frizzle_phone.rtp.stream import RtpStream
from frizzle_phone.sip.message import (
    SipMessage,
    build_request,
    build_response,
    extract_branch,
    extract_extension,
    generate_branch,
    generate_tag,
    parse_message,
    parse_via_params,
)
from frizzle_phone.sip.sdp import build_sdp_answer, parse_sdp_offer
from frizzle_phone.sip.transaction import InviteServerTxn, TxnState

logger = logging.getLogger(__name__)

_HandlerType = Callable[["SipMessage", tuple[str, int], tuple[str, int]], None]

ALLOWED_METHODS = (
    "INVITE, ACK, BYE, CANCEL, REGISTER, OPTIONS, REFER, SUBSCRIBE, NOTIFY"
)


def _add_via_received_params(msg: SipMessage, addr: tuple[str, int]) -> None:
    """Add received/rport Via params per RFC 3581 §4.

    Mutates ``msg.headers`` in-place, tagging the top Via with the
    observed source IP and port.  Only fills ``rport`` when the client
    requested it (i.e. the Via already contains an empty ``rport`` parameter).

    RFC 3261 §18.2.1 requires the ``received`` parameter when the Via
    sent-by differs from the packet source address.  RFC 3581 §4 extends
    this with the ``rport`` parameter so the server can reply to the
    observed source port (symmetric response routing for NAT traversal).
    """
    for i, (key, value) in enumerate(msg.headers):
        if key.lower() == "via":
            params = parse_via_params(value)
            client_rport = "rport" in params
            if client_rport:
                # Strip the empty rport param; we add it back with the value
                parts = value.split(";")
                parts = [p for p in parts if not p.strip().startswith("rport")]
                value = ";".join(parts)
            value = f"{value};received={addr[0]}"
            if client_rport:
                value += f";rport={addr[1]}"
            msg.headers[i] = (key, value)
            return


def _compute_response_addr(msg: SipMessage, addr: tuple[str, int]) -> tuple[str, int]:
    """Determine response address per RFC 3261 §18.2.2 and RFC 3581 §4.

    When the client included ``rport`` in its Via, responses go to the
    observed source port.  Otherwise, fall back to the Via sent-by port.
    """
    via = msg.header("Via")
    if via is None:
        return addr

    params = parse_via_params(via)

    # RFC 3581 §4: if rport is present with a value, respond to observed
    # source port (enables symmetric response routing through NATs)
    rport = params.get("rport")
    if rport:
        try:
            return (addr[0], int(rport))
        except ValueError:
            pass

    # RFC 3261 §18.2.2: for unreliable unicast transports, if "received"
    # is set, send to that address using the port from "sent-by" (or 5060)
    sent_by = via.split(";")[0].strip()
    parts = sent_by.split(None, 1)
    if len(parts) < 2:
        return addr
    host_port = parts[1]

    if ":" in host_port:
        port_str = host_port.rsplit(":", 1)[1]
        try:
            port = int(port_str)
        except ValueError:
            return addr
    else:
        port = 5060

    return (addr[0], port)


@dataclasses.dataclass
class Call:
    call_id: str
    from_tag: str
    to_tag: str
    remote_addr: tuple[str, int]
    remote_contact: str
    remote_from: str
    remote_rtp_addr: tuple[str, int]
    audio_buf: bytes
    rtp_port: int = 0
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

    def __init__(self, *, server_ip: str, audio_routes: dict[str, bytes]) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._calls: dict[str, Call] = {}
        self._invite_txns: dict[str, InviteServerTxn] = {}
        self._rtp_tasks: set[asyncio.Task[None]] = set()
        self._server_ip = server_ip
        self._audio_routes = audio_routes
        self._handlers: dict[str, _HandlerType] = {
            "REGISTER": self._handle_register,
            "INVITE": self._handle_invite,
            "ACK": self._handle_ack,
            "BYE": self._handle_bye,
            "CANCEL": self._handle_cancel,
            "OPTIONS": self._handle_options,
            "REFER": self._handle_stub_200,
            "SUBSCRIBE": self._handle_stub_200,
            "NOTIFY": self._handle_stub_200,
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
            msg = parse_message(data)
        except Exception:
            logger.exception("Failed to parse SIP message from %s", addr)
            return

        logger.info("Received %s from %s", msg.method, addr)

        # RFC 3581 §4 / RFC 3261 §18.2.1: tag Via with observed source address
        _add_via_received_params(msg, addr)
        # RFC 3261 §18.2.2: determine response destination from Via header
        resp_addr = _compute_response_addr(msg, addr)

        # RFC 3261 §8.2.2.3: reject unsupported Require options with 420
        # and echo them in an Unsupported header. ACK and CANCEL are exempt
        # per §8.2.2.3 ("MUST NOT be used in a SIP CANCEL request, or in
        # an ACK request sent for a non-2xx response").
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

        # RFC 3261 §17.2.1: if a retransmitted INVITE matches an existing
        # server transaction (by Via branch), retransmit the most recent
        # provisional or final response rather than re-processing.
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
            # RFC 3261 §8.2.1: UAS MUST respond 405 for methods it does
            # not support, and MUST include an Allow header listing
            # the supported methods.
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

    def _handle_register(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        logger.debug("REGISTER from %s:\n%s", addr, msg.headers)
        contact = msg.header("Contact")
        extra: list[tuple[str, str]] = []
        # RFC 3261 §10.3 step 8: 200 OK MUST contain Contact headers
        # enumerating current bindings, each with an "expires" parameter.
        if contact is not None:
            extra.append(("Contact", f"{contact};expires=3600"))
        # RFC 3261 §10.3 step 7: use the request's Expires value if
        # present, otherwise fall back to a locally-configured default.
        expires = msg.header("Expires") or "3600"
        extra.append(("Expires", expires))
        # RFC 3261 §10.3 step 8: return 200 OK with current bindings
        response = build_response(
            msg,
            200,
            "OK",
            to_tag=generate_tag(),
            extra_headers=extra,
        )
        logger.debug("REGISTER response:\n%s", response.decode())
        self._send(response, resp_addr)

    def _parse_invite_params(
        self, msg: SipMessage, addr: tuple[str, int]
    ) -> tuple[str, str, tuple[str, int], str, str]:
        """Extract call parameters from an INVITE request.

        Returns (call_id, from_tag, remote_rtp_addr, remote_contact).
        """
        call_id = msg.header("Call-ID") or ""
        from_header = msg.header("From") or ""
        from_tag = ""
        if ";tag=" in from_header:
            from_tag = from_header.split(";tag=")[1].split(";")[0]

        remote_rtp_addr = (addr[0], 0)
        if msg.body:
            offer = parse_sdp_offer(msg.body)
            remote_rtp_addr = (offer.connection_address, offer.audio_port)

        contact_header = msg.header("Contact") or f"<sip:{addr[0]}:{addr[1]}>"
        # Extract URI from between angle brackets, ignoring Contact params
        if "<" in contact_header and ">" in contact_header:
            remote_contact = contact_header[
                contact_header.index("<") + 1 : contact_header.index(">")
            ]
        else:
            remote_contact = contact_header

        remote_from = from_header.split(";tag=")[0].strip()
        return call_id, from_tag, remote_rtp_addr, remote_contact, remote_from

    def _setup_invite_txn(
        self, call: Call, response: bytes, resp_addr: tuple[str, int], branch: str
    ) -> None:
        """Create an INVITE server transaction and send the 200 OK.

        RFC 3261 §13.3.1.4: 2xx responses are retransmitted by the TU
        (not the transaction layer) at intervals starting at T1 and
        doubling up to T2, until an ACK is received. If no ACK arrives
        within 64*T1 seconds, the session SHOULD be terminated with BYE.
        """
        old_txn = self._invite_txns.pop(branch, None)
        if old_txn is not None:
            old_txn.terminate()
        loop = asyncio.get_running_loop()
        txn = InviteServerTxn(
            branch=branch,
            transport=self._transport,  # type: ignore[arg-type]
            loop=loop,
            on_timeout=lambda: loop.call_soon(self._send_bye, call),
            on_terminated=self._remove_txn,
        )
        self._invite_txns[branch] = txn
        call.invite_branch = branch
        txn.send_2xx(response, resp_addr)

    def _handle_invite(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        # RFC 3261 §8.2.2.1: if the Request-URI does not identify an
        # address the UAS is willing to accept requests for, respond 404.
        extension = extract_extension(msg.uri)
        audio_buf = self._audio_routes.get(extension)
        if audio_buf is None:
            logger.info("Unknown extension %r → 404", extension)
            self._send(
                build_response(msg, 404, "Not Found", to_tag=generate_tag()),
                resp_addr,
            )
            return

        call_id, from_tag, remote_rtp_addr, remote_contact, remote_from = (
            self._parse_invite_params(msg, addr)
        )
        # RFC 3261 §8.2.6.2: UAS MUST add a tag to the To header field in
        # responses (except 100 Trying). The same tag is used for all
        # responses within this INVITE transaction.
        to_tag = generate_tag()

        # Clean up existing call if re-INVITE
        existing = self._calls.get(call_id)
        if existing is not None:
            self._terminate_call(existing)

        rtp_port = self._reserve_rtp_port()
        call = Call(
            call_id=call_id,
            from_tag=from_tag,
            to_tag=to_tag,
            remote_addr=resp_addr,
            remote_contact=remote_contact,
            remote_from=remote_from,
            remote_rtp_addr=remote_rtp_addr,
            audio_buf=audio_buf,
            rtp_port=rtp_port,
            invite_request=msg,
        )
        self._calls[call_id] = call

        # RFC 3261 §17.2.1: send 100 Trying immediately to quench
        # INVITE retransmissions. §17.2.1: tag insertion in the To
        # field of 100 is downgraded from MAY to SHOULD NOT, so no
        # to_tag here.
        self._send(build_response(msg, 100, "Trying"), resp_addr)

        # RFC 3261 §13.3.1.4: 2xx response with SDP answer establishes
        # the session. Contact header required per §12.1.1 so the peer
        # can route subsequent in-dialog requests (ACK, BYE) to us.
        ok = build_response(
            msg,
            200,
            "OK",
            body=build_sdp_answer(self._server_ip, rtp_port),
            to_tag=to_tag,
            extra_headers=[
                ("Contact", f"<sip:frizzle@{self._server_ip}:5060>"),
                ("Allow", ALLOWED_METHODS),
            ],
        )

        invite_branch = extract_branch(msg)
        if invite_branch and self._transport is not None:
            self._setup_invite_txn(call, ok, resp_addr, invite_branch)
        else:
            self._send(ok, resp_addr)

    def _handle_ack(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        # RFC 3261 §13.3.1.4: ACK for a 2xx is generated by the UAC core
        # (not the transaction layer) and arrives as a new request with no
        # matching server transaction (§18.2.1).
        call_id = msg.header("Call-ID") or ""
        call = self._calls.get(call_id)
        if call is None:
            logger.warning("ACK for unknown call: %s", call_id)
            return

        # RFC 3261 §13.3.1.4: ACK receipt stops 2xx retransmission
        # (Timer G in the INVITE server transaction).
        if call.invite_branch and call.invite_branch in self._invite_txns:
            self._invite_txns[call.invite_branch].receive_ack()

        if call.rtp_stream is None:
            self._start_rtp_for_call(call)

    def _handle_bye(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        call_id = msg.header("Call-ID") or ""
        call = self._calls.pop(call_id, None)
        if call is None:
            # RFC 3261 §15.1.2: BYE that does not match an existing
            # dialog SHOULD be rejected with 481.
            response = build_response(msg, 481, "Call/Transaction Does Not Exist")
            self._send(response, resp_addr)
            return
        self._terminate_call(call)
        # RFC 3261 §15.1.2: UAS MUST generate a 2xx response to a
        # valid BYE and pass it to the server transaction.
        response = build_response(msg, 200, "OK", to_tag=call.to_tag)
        self._send(response, resp_addr)

    def _handle_cancel(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        call_id = msg.header("Call-ID") or ""

        call = self._calls.get(call_id)
        if call is None:
            # RFC 3261 §9.2: if no matching transaction is found, respond 481
            no_match = build_response(msg, 481, "Call/Transaction Does Not Exist")
            self._send(no_match, resp_addr)
            return

        # RFC 3261 §9.2: "If [the UAS] has [sent a final response], the
        # CANCEL request has no effect on the processing of the original
        # request." Acknowledge the CANCEL with 200 but do not tear down.
        if call.invite_branch:
            txn = self._invite_txns.get(call.invite_branch)
            if txn is not None and txn.state != TxnState.PROCEEDING:
                ok = build_response(msg, 200, "OK", to_tag=call.to_tag)
                self._send(ok, resp_addr)
                return

        # RFC 3261 §9.2: CANCEL matched a transaction still in PROCEEDING.
        # First, respond 200 OK to the CANCEL itself.
        self._calls.pop(call_id, None)
        ok = build_response(msg, 200, "OK", to_tag=call.to_tag)
        self._send(ok, resp_addr)

        # RFC 3261 §9.2: "If the original request was an INVITE, the UAS
        # SHOULD immediately respond to the INVITE with a 487 (Request
        # Terminated)." Sent before terminating the transaction so
        # retransmission state is still alive for delivery.
        if call.invite_request is not None:
            terminated = build_response(
                call.invite_request,
                487,
                "Request Terminated",
                to_tag=call.to_tag,
            )
            self._send(terminated, resp_addr)
        self._terminate_call(call)

    def _handle_options(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        """Respond to OPTIONS (used as keepalive by Cisco phones).

        RFC 3261 §11.2: response code MUST match what the UAS would return
        for an INVITE.  Allow header SHOULD be present in the 200 OK.
        """
        response = build_response(
            msg,
            200,
            "OK",
            to_tag=generate_tag(),
            extra_headers=[("Allow", ALLOWED_METHODS)],
        )
        self._send(response, resp_addr)

    def _handle_stub_200(
        self,
        msg: SipMessage,
        addr: tuple[str, int],
        resp_addr: tuple[str, int],
    ) -> None:
        """Accept REFER/SUBSCRIBE/NOTIFY with a simple 200 OK."""
        response = build_response(msg, 200, "OK", to_tag=generate_tag())
        self._send(response, resp_addr)

    @staticmethod
    def _reserve_rtp_port() -> int:
        """Bind a UDP socket to get an OS-assigned port, then release it."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def _start_rtp_for_call(self, call: Call) -> None:
        """Create an RTP stream for the call and schedule BYE on completion."""
        loop = asyncio.get_running_loop()
        call.rtp_stream = RtpStream(
            loop=loop,
            remote_addr=call.remote_rtp_addr,
            audio_buf=call.audio_buf,
            local_port=call.rtp_port,
        )
        task = loop.create_task(call.rtp_stream.start())
        task.add_done_callback(lambda _f: loop.call_soon(self._send_bye, call))
        self._rtp_tasks.add(task)
        task.add_done_callback(self._rtp_tasks.discard)

    def _send_bye(self, call: Call) -> None:
        """Send a BYE to the remote phone after audio finishes."""
        # Guard against double-BYE
        if call.terminated:
            return
        self._terminate_call(call)

        call_id = call.call_id
        remote_addr = call.remote_addr
        self._calls.pop(call_id, None)

        # RFC 3261 §12.2.1.1: in-dialog requests use the remote target
        # URI as the Request-URI.
        bye_msg = build_request(
            "BYE",
            call.remote_contact,
            headers=[
                (
                    "Via",
                    f"SIP/2.0/UDP {self._server_ip}:5060;branch={generate_branch()}",
                ),
                # RFC 3261 §12.2.1.1: From URI/tag = local URI/tag,
                # To URI/tag = remote URI/tag. Since we are the UAS that
                # accepted the INVITE, our local tag is the To tag from
                # the original INVITE's 200 OK.
                ("From", f"<sip:frizzle@{self._server_ip}>;tag={call.to_tag}"),
                ("To", f"{call.remote_from};tag={call.from_tag}"),
                ("Call-ID", call_id),
                # RFC 3261 §12.2.1.1: CSeq MUST be strictly monotonically
                # increasing; method field MUST match the request method.
                ("CSeq", "1 BYE"),
                # RFC 3261 §8.1.1.6: Max-Forwards SHOULD start at 70
                ("Max-Forwards", "70"),
            ],
        )
        self._send(bye_msg, remote_addr)
        logger.info("Sent BYE for call %s", call_id)

    def _remove_txn(self, branch: str) -> None:
        """Callback for transaction cleanup after termination."""
        self._invite_txns.pop(branch, None)

    def graceful_shutdown(self) -> None:
        """Send BYE to all active calls before tearing down state.

        Must be called while the transport is still open so the BYEs
        can actually be sent on the wire.
        """
        for call in list(self._calls.values()):
            self._send_bye(call)

    def _cleanup_all_calls(self) -> None:
        """Stop all active calls and transactions during shutdown."""
        calls = list(self._calls.values())
        self._calls.clear()
        for call in calls:
            self._terminate_call(call)
        # Terminate any orphaned transactions not linked to a call
        remaining_txns = list(self._invite_txns.values())
        self._invite_txns.clear()
        for txn in remaining_txns:
            txn.terminate()
        for task in self._rtp_tasks:
            task.cancel()
        self._rtp_tasks.clear()


async def start_server(
    host: str = "0.0.0.0",
    port: int = 5060,
    *,
    server_ip: str,
    audio_routes: dict[str, bytes],
) -> tuple[asyncio.DatagramTransport, SipServer]:
    loop = asyncio.get_running_loop()
    server = SipServer(server_ip=server_ip, audio_routes=audio_routes)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: server, local_addr=(host, port)
    )
    assert isinstance(transport, asyncio.DatagramTransport)
    logger.info("Listening on %s:%d", host, port)
    return transport, server
