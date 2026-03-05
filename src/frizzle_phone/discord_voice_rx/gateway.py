"""Gateway hook for voice websocket events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger(__name__)

# Voice WS opcodes
READY = 2
SESSION_DESCRIPTION = 4
SPEAKING = 5
CLIENT_DISCONNECT = 13
DAVE_PREPARE_EPOCH = 24


async def hook(ws, msg: dict[str, Any]) -> None:
    """Process voice websocket messages for SSRC tracking."""
    op: int = msg["op"]
    data: dict[str, Any] = msg.get("d", {})
    vc = ws._connection.voice_client

    if op == READY:
        ssrc = data["ssrc"]
        own_id = vc.guild.me.id
        vc._add_ssrc(own_id, ssrc)

    elif op == SPEAKING:
        uid = int(data["user_id"])
        ssrc = data["ssrc"]
        vc._add_ssrc(uid, ssrc)

    elif op == CLIENT_DISCONNECT:
        uid = int(data["user_id"])
        vc._remove_ssrc(user_id=uid)

    elif op == SESSION_DESCRIPTION:
        vc._update_secret_key()

    elif op == DAVE_PREPARE_EPOCH:
        dave_session = getattr(ws._connection, "dave_session", None)
        if dave_session is not None:
            dave_session.set_passthrough_mode(True, 10)
            log.debug("Enabled DAVE passthrough for epoch transition")
