"""SIP UDP server with call state management."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import socket
import string

from frizzle_phone.rtp.pcmu import generate_rhythm
from frizzle_phone.rtp.stream import RtpStream
from frizzle_phone.sip.message import SipMessage, build_response, parse_request
from frizzle_phone.sip.sdp import build_sdp_answer

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Call:
    call_id: str
    from_tag: str
    remote_addr: tuple[str, int]
    rtp_stream: RtpStream | None = None
    invite_request: SipMessage | None = None


def get_server_ip() -> str:
    """Detect the local IP address by opening a UDP socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


def _generate_tag() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _generate_branch() -> str:
    chars = string.ascii_lowercase + string.digits
    return "z9hG4bK" + "".join(random.choices(chars, k=8))


class SipServer(asyncio.DatagramProtocol):
    """SIP server handling REGISTER, INVITE, ACK, BYE, and CANCEL."""

    def __init__(self) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._calls: dict[str, Call] = {}
        self._server_ip = get_server_ip()
        self._audio_buf = generate_rhythm(60.0)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]
        logger.info("SIP server listening")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = parse_request(data)
        except Exception:
            logger.exception("Failed to parse SIP message from %s", addr)
            return

        logger.info("Received %s from %s", msg.method, addr)

        handler = {
            "REGISTER": self._handle_register,
            "INVITE": self._handle_invite,
            "ACK": self._handle_ack,
            "BYE": self._handle_bye,
            "CANCEL": self._handle_cancel,
        }.get(msg.method)

        if handler is not None:
            handler(msg, addr)
        else:
            logger.warning("Unhandled method: %s", msg.method)

    def _send(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._transport is not None:
            self._transport.sendto(data, addr)

    def _handle_register(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        response = build_response(msg, 200, "OK")
        self._send(response, addr)

    def _handle_invite(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        call_id = msg.header("Call-ID") or ""
        from_header = msg.header("From") or ""
        from_tag = ""
        if ";tag=" in from_header:
            from_tag = from_header.split(";tag=")[1].split(";")[0]

        call = Call(
            call_id=call_id,
            from_tag=from_tag,
            remote_addr=addr,
            invite_request=msg,
        )
        self._calls[call_id] = call

        # 100 Trying
        trying = build_response(msg, 100, "Trying")
        self._send(trying, addr)

        # 200 OK with SDP
        sdp = build_sdp_answer(self._server_ip)
        ok = build_response(msg, 200, "OK", body=sdp)
        self._send(ok, addr)

    def _handle_ack(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        call_id = msg.header("Call-ID") or ""
        call = self._calls.get(call_id)
        if call is None:
            logger.warning("ACK for unknown call: %s", call_id)
            return

        loop = asyncio.get_event_loop()
        done_future: asyncio.Future[None] = loop.create_future()
        call.rtp_stream = RtpStream(
            loop=loop,
            remote_addr=(addr[0], 10000),
            audio_buf=self._audio_buf,
            done_callback=done_future,
        )
        done_future.add_done_callback(lambda _f: self._send_bye(call))
        asyncio.ensure_future(call.rtp_stream.start())

    def _handle_bye(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        call_id = msg.header("Call-ID") or ""
        call = self._calls.pop(call_id, None)
        if call is not None and call.rtp_stream is not None:
            call.rtp_stream.stop()
        response = build_response(msg, 200, "OK")
        self._send(response, addr)

    def _handle_cancel(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        call_id = msg.header("Call-ID") or ""

        # 200 OK for the CANCEL itself
        ok = build_response(msg, 200, "OK")
        self._send(ok, addr)

        call = self._calls.pop(call_id, None)
        if call is not None:
            if call.rtp_stream is not None:
                call.rtp_stream.stop()
            # 487 Request Terminated for the original INVITE
            if call.invite_request is not None:
                terminated = build_response(
                    call.invite_request,
                    487,
                    "Request Terminated",
                )
                self._send(terminated, addr)

    def _send_bye(self, call: Call) -> None:
        """Send a BYE to the remote phone after audio finishes."""
        if call.rtp_stream is not None:
            call.rtp_stream.stop()

        call_id = call.call_id
        remote_addr = call.remote_addr
        self._calls.pop(call_id, None)

        via_branch = _generate_branch()
        tag = _generate_tag()
        lines = [
            f"BYE sip:{remote_addr[0]}:{remote_addr[1]} SIP/2.0",
            f"Via: SIP/2.0/UDP {self._server_ip}:5060;branch={via_branch}",
            f"From: <sip:frizzle@{self._server_ip}>;tag={tag}",
            f"To: <sip:{remote_addr[0]}>",
            f"Call-ID: {call_id}",
            "CSeq: 1 BYE",
            "Content-Length: 0",
            "",
            "",
        ]
        bye_msg = "\r\n".join(lines).encode("utf-8")
        self._send(bye_msg, remote_addr)
        logger.info("Sent BYE for call %s", call_id)


async def start_server(
    host: str = "0.0.0.0",
    port: int = 5060,
) -> asyncio.DatagramTransport:
    loop = asyncio.get_event_loop()
    transport, _ = await loop.create_datagram_endpoint(
        SipServer, local_addr=(host, port)
    )
    logger.info("Listening on %s:%d", host, port)
    return transport  # pyright: ignore[reportReturnType]
