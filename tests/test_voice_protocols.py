"""Verify BridgeableVoiceClient protocol conformance."""

from __future__ import annotations

import socket

import numpy as np
import pytest

from frizzle_phone.bridge_manager import BridgeManager
from frizzle_phone.voice_protocols import BridgeableVoiceClient, VoiceConnector


class _Dummy:
    """Minimal class that should satisfy BridgeableVoiceClient."""

    def play(self, source: object) -> None:
        pass

    def start_listening(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def stop_listening(self) -> None:
        pass

    def pop_tick(self) -> dict[int, np.ndarray]:
        return {}

    def is_connected(self) -> bool:
        return True

    async def disconnect(self, *, force: bool = False) -> None:
        pass


def test_dummy_satisfies_protocol() -> None:
    """A class with the right methods is accepted as BridgeableVoiceClient."""
    client: BridgeableVoiceClient = _Dummy()
    assert client.pop_tick() == {}
    assert client.is_connected() is True


@pytest.mark.asyncio
async def test_bridge_manager_accepts_fake_voice_client() -> None:
    """BridgeManager.start() accepts a BridgeableVoiceClient,
    not just VoiceRecvClient.
    """

    class FakeVC:
        def __init__(self) -> None:
            self._playing = False
            self._listening = False

        def play(self, source: object) -> None:
            self._playing = True

        def start_listening(self) -> None:
            self._listening = True

        def stop(self) -> None:
            self._playing = False
            self._listening = False

        def stop_listening(self) -> None:
            self._listening = False

        def pop_tick(self) -> dict[int, np.ndarray]:
            return {}

        def is_connected(self) -> bool:
            return True

        async def disconnect(self, *, force: bool = False) -> None:
            pass

    mgr = BridgeManager()
    fake_vc = FakeVC()

    # Find a free UDP port via an ephemeral socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    rtp_port = sock.getsockname()[1]
    sock.close()

    handle = await mgr.start(fake_vc, rtp_port, ("127.0.0.1", 0))
    assert fake_vc._playing is True
    assert fake_vc._listening is True
    handle.stop()
    mgr.shutdown()


class _FakeConnector:
    async def connect(self, guild_id: int, channel_id: int) -> _Dummy:
        return _Dummy()


def test_fake_connector_satisfies_protocol() -> None:
    connector: VoiceConnector = _FakeConnector()
    assert connector is not None
