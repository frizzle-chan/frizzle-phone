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


async def hook(ws, msg: dict[str, Any]) -> None:
    """Process voice websocket messages for SSRC tracking."""
    op: int = msg["op"]
    data: dict[str, Any] = msg.get("d", {})
    vc = ws._connection.voice_client

    if op == ws.READY:
        ssrc = data["ssrc"]
        own_id = vc.guild.me.id
        vc._ssrc_to_id[ssrc] = own_id
        vc._id_to_ssrc[own_id] = ssrc

    elif op == SPEAKING:
        uid = int(data["user_id"])
        ssrc = data["ssrc"]
        vc._ssrc_to_id[ssrc] = uid
        vc._id_to_ssrc[uid] = ssrc
        if vc._decoder_thread:
            vc._decoder_thread.set_ssrc_user(ssrc, uid)

    elif op == CLIENT_DISCONNECT:
        uid = int(data["user_id"])
        ssrc = vc._id_to_ssrc.get(uid)
        if ssrc is not None:
            vc._ssrc_to_id.pop(ssrc, None)
            vc._id_to_ssrc.pop(uid, None)
            if vc._decoder_thread:
                vc._decoder_thread.destroy_decoder(ssrc=ssrc, user_id=uid)

    elif op == ws.SESSION_DESCRIPTION:
        vc._update_secret_key()
