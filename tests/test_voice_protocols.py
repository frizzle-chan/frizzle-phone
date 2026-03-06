"""Verify BridgeableVoiceClient protocol conformance."""

from __future__ import annotations

import numpy as np

from frizzle_phone.voice_protocols import BridgeableVoiceClient


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
