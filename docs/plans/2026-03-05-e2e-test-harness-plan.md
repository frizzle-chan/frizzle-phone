# E2E Test Harness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an E2E test harness that exercises the full SIP → bridge → RTP audio pipeline with 5 simulated Discord speakers, replacing the Discord voice connection with a deterministic fake.

**Architecture:** Introduce two protocols (`BridgeableVoiceClient`, `VoiceConnector`) to create a seam between SIP/bridge code and the Discord voice client. A `FakeVoiceRecvClient` produces synthetic multi-speaker audio, injected via a `FakeVoiceConnector` into a real `SipServer` running over real UDP.

**Tech Stack:** Python asyncio, pytest-asyncio, aiosqlite (in-memory), numpy, soxr, pytest-regressions (golden files)

---

### Task 1: Add BridgeableVoiceClient protocol

The `BridgeManager` and `SipServer` currently depend on the concrete `VoiceRecvClient` class. This task introduces a protocol type so the fake can be injected.

**Files:**
- Create: `src/frizzle_phone/voice_protocols.py`
- Test: `tests/test_voice_protocols.py`

**Step 1: Write the failing test**

Create `tests/test_voice_protocols.py`:

```python
"""Verify FakeVoiceRecvClient satisfies BridgeableVoiceClient at type-check time."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from frizzle_phone.voice_protocols import BridgeableVoiceClient

if TYPE_CHECKING:
    pass


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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_protocols.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'frizzle_phone.voice_protocols'`

**Step 3: Write minimal implementation**

Create `src/frizzle_phone/voice_protocols.py`:

```python
"""Protocols for voice client abstraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np


@runtime_checkable
class BridgeableVoiceClient(Protocol):
    """Interface for voice clients used by BridgeManager and SipServer."""

    def play(self, source: object) -> None: ...
    def start_listening(self) -> None: ...
    def stop(self) -> None: ...
    def stop_listening(self) -> None: ...
    def pop_tick(self) -> dict[int, np.ndarray]: ...
    def is_connected(self) -> bool: ...
    async def disconnect(self, *, force: bool = False) -> None: ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_voice_protocols.py -v`
Expected: PASS

**Step 5: Run full checks**

Run: `just`
Expected: All checks pass (lint, format, types, vulture)

**Step 6: Commit**

```
feat: add BridgeableVoiceClient protocol

Introduces a Protocol type for voice clients so BridgeManager and
SipServer can accept both VoiceRecvClient and test fakes.
```

---

### Task 2: Update BridgeManager to use BridgeableVoiceClient

**Files:**
- Modify: `src/frizzle_phone/bridge_manager.py:10-11` (import), `:27` (type hint), `:50` (type hint)

**Step 1: Write the failing test**

Add to `tests/test_voice_protocols.py`:

```python
from frizzle_phone.bridge_manager import BridgeHandle, BridgeManager
import asyncio
import pytest


@pytest.mark.asyncio
async def test_bridge_manager_accepts_fake_voice_client() -> None:
    """BridgeManager.start() should accept a BridgeableVoiceClient, not just VoiceRecvClient."""
    loop = asyncio.get_running_loop()

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

    # Create a throwaway UDP endpoint for RTP receive
    recv_transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    rtp_port = recv_transport.get_extra_info("sockname")[1]
    recv_transport.close()

    handle = await mgr.start(fake_vc, rtp_port, ("127.0.0.1", 0))
    assert fake_vc._playing is True
    assert fake_vc._listening is True
    handle.stop()
    mgr.shutdown()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_protocols.py::test_bridge_manager_accepts_fake_voice_client -v`
Expected: Might pass at runtime (duck typing) but ty type checker will complain. The point is to update the type hints.

**Step 3: Update BridgeManager type hints**

In `src/frizzle_phone/bridge_manager.py`:

- Replace `from frizzle_phone.discord_voice_rx import VoiceRecvClient` with `from frizzle_phone.voice_protocols import BridgeableVoiceClient`
- Change `BridgeHandle.__init__` parameter `voice_client: VoiceRecvClient` → `voice_client: BridgeableVoiceClient`
- Change `BridgeManager.start` parameter `voice_client: VoiceRecvClient` → `voice_client: BridgeableVoiceClient`

**Step 4: Run tests and checks**

Run: `just`
Expected: All pass. Existing `test_bridge_manager.py` still passes (VoiceRecvClient satisfies the protocol).

**Step 5: Commit**

```
refactor: BridgeManager accepts BridgeableVoiceClient protocol

Loosens the type hint from concrete VoiceRecvClient to the
BridgeableVoiceClient protocol, enabling test fakes.
```

---

### Task 3: Add VoiceConnector protocol and default implementation

Extract the guild lookup + `channel.connect()` logic from `_handle_invite_async` into a `VoiceConnector` protocol with a `DiscordVoiceConnector` default implementation.

**Files:**
- Modify: `src/frizzle_phone/voice_protocols.py` (add VoiceConnector protocol)
- Create: `src/frizzle_phone/voice_connector.py` (DiscordVoiceConnector)
- Modify: `src/frizzle_phone/sip/server.py:185-215` (accept voice_connector param), `:559-605` (delegate to connector)
- Test: `tests/test_voice_protocols.py` (add VoiceConnector test)

**Step 1: Write the failing test**

Add to `tests/test_voice_protocols.py`:

```python
from frizzle_phone.voice_protocols import VoiceConnector


class _FakeConnector:
    async def connect(self, guild_id: int, channel_id: int) -> _Dummy:
        return _Dummy()


def test_fake_connector_satisfies_protocol() -> None:
    connector: VoiceConnector = _FakeConnector()
    assert connector is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_voice_protocols.py::test_fake_connector_satisfies_protocol -v`
Expected: FAIL — `ImportError: cannot import name 'VoiceConnector'`

**Step 3: Add VoiceConnector protocol**

Add to `src/frizzle_phone/voice_protocols.py`:

```python
class VoiceConnector(Protocol):
    """Interface for connecting to a voice channel."""

    async def connect(
        self, guild_id: int, channel_id: int
    ) -> BridgeableVoiceClient: ...
```

**Step 4: Create DiscordVoiceConnector**

Create `src/frizzle_phone/voice_connector.py`:

```python
"""Default VoiceConnector that connects to Discord voice channels."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from frizzle_phone.discord_voice_rx import VoiceRecvClient

logger = logging.getLogger(__name__)


class DiscordVoiceConnector:
    """Connects to Discord voice channels via bot.get_guild/channel.connect."""

    def __init__(self, bot: commands.Bot) -> None:
        self._bot = bot

    async def connect(
        self, guild_id: int, channel_id: int
    ) -> VoiceRecvClient:
        guild = self._bot.get_guild(guild_id)
        if guild is None:
            raise ConnectionError(f"Guild {guild_id} not found")
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.VoiceChannel):
            raise ConnectionError(f"Voice channel {channel_id} not found in guild {guild_id}")
        return await asyncio.wait_for(
            channel.connect(cls=VoiceRecvClient),
            timeout=10.0,
        )
```

**Step 5: Update SipServer to accept voice_connector**

In `src/frizzle_phone/sip/server.py`:

Add import at top:
```python
from frizzle_phone.voice_connector import DiscordVoiceConnector
from frizzle_phone.voice_protocols import VoiceConnector
```

Update `SipServer.__init__` signature (add after `bridge_manager` param):
```python
    voice_connector: VoiceConnector | None = None,
```

In `__init__` body, after `self._bridge_manager` assignment:
```python
    self._voice_connector = voice_connector or DiscordVoiceConnector(bot)
```

Replace `_handle_invite_async` lines 559-605 (the guild lookup + channel.connect block) with:

```python
        if result.guild_id is not None:
            if result.channel_id is None:
                raise ValueError("Discord extension missing channel_id")
            try:
                vc = await self._voice_connector.connect(
                    result.guild_id, result.channel_id
                )
                if call.terminated:
                    vc.stop()
                    self._fire_and_forget(
                        vc.disconnect(),
                        name=f"vc-disconnect-{call.call_id}",
                    )
                    return
                call.pending_bridge = PendingBridge(
                    voice_client=vc,
                    guild_id=result.guild_id,
                    channel_id=result.channel_id,
                )
            except Exception:
                logger.exception(
                    "Failed to connect to voice channel %s", result.channel_id
                )
                self._calls.pop(call_id, None)
                self._send(
                    build_response(msg, 503, "Service Unavailable", to_tag=to_tag),
                    resp_addr,
                )
                return
```

Also update `PendingBridge` and `DiscordBridgeContext` dataclass type hints from `VoiceRecvClient` to `BridgeableVoiceClient`:
```python
from frizzle_phone.voice_protocols import BridgeableVoiceClient

@dataclasses.dataclass
class PendingBridge:
    voice_client: BridgeableVoiceClient
    ...

@dataclasses.dataclass
class DiscordBridgeContext:
    voice_client: BridgeableVoiceClient
    ...
```

Remove the now-unused `discord.VoiceChannel` import and `VoiceRecvClient` import from server.py (if no longer directly referenced).

Update `start_server()` to accept and pass through `voice_connector`:
```python
async def start_server(
    host: str = "0.0.0.0",
    port: int = 5060,
    *,
    server_ip: str,
    db: aiosqlite.Connection,
    audio_buffers: dict[str, bytes],
    bot: commands.Bot,
    voice_connector: VoiceConnector | None = None,
) -> tuple[asyncio.DatagramTransport, SipServer]:
    loop = asyncio.get_running_loop()
    server = SipServer(
        server_ip=server_ip,
        db=db,
        audio_buffers=audio_buffers,
        bot=bot,
        voice_connector=voice_connector,
    )
    ...
```

**Step 6: Run tests and checks**

Run: `just`
Expected: All existing tests pass. The `test_e2e_sip.py` tests pass because they use a `MagicMock` bot and never exercise the discord path (they use audio extensions, not discord extensions).

**Step 7: Commit**

```
refactor: extract VoiceConnector protocol from SipServer

Moves guild lookup and channel.connect() into DiscordVoiceConnector,
injected via VoiceConnector protocol. SipServer no longer directly
depends on discord.VoiceChannel or VoiceRecvClient for voice connect.
```

---

### Task 4: Build FakeVoiceRecvClient

The test fake that produces deterministic multi-speaker audio.

**Files:**
- Create: `tests/fake_voice.py`
- Test: `tests/test_fake_voice.py`

**Step 1: Write the failing test**

Create `tests/test_fake_voice.py`:

```python
"""Tests for the FakeVoiceRecvClient test helper."""

from __future__ import annotations

import numpy as np
import pytest

from tests.fake_voice import FakeVoiceRecvClient, sine_tone_speakers


def test_sine_tone_speakers_returns_5_speakers() -> None:
    speakers = sine_tone_speakers(n_ticks=10)
    assert len(speakers) == 5
    for user_id, frames in speakers.items():
        assert len(frames) == 10
        assert frames[0].shape == (960,)
        assert frames[0].dtype == np.int16


def test_pop_tick_returns_all_speakers() -> None:
    speakers = sine_tone_speakers(n_ticks=3)
    fake = FakeVoiceRecvClient(speakers)
    fake.start_listening()

    tick = fake.pop_tick()
    assert len(tick) == 5
    for user_id, frame in tick.items():
        assert frame.shape == (960,)


def test_pop_tick_exhausts_frames() -> None:
    speakers = sine_tone_speakers(n_ticks=2)
    fake = FakeVoiceRecvClient(speakers)
    fake.start_listening()

    tick1 = fake.pop_tick()
    assert len(tick1) == 5
    tick2 = fake.pop_tick()
    assert len(tick2) == 5
    tick3 = fake.pop_tick()
    assert tick3 == {}


def test_pop_tick_before_start_listening_returns_empty() -> None:
    speakers = sine_tone_speakers(n_ticks=3)
    fake = FakeVoiceRecvClient(speakers)
    assert fake.pop_tick() == {}


def test_play_accepts_source() -> None:
    fake = FakeVoiceRecvClient({})
    fake.play(object())  # should not raise


def test_is_connected_returns_true() -> None:
    fake = FakeVoiceRecvClient({})
    assert fake.is_connected() is True


@pytest.mark.asyncio
async def test_disconnect_is_noop() -> None:
    fake = FakeVoiceRecvClient({})
    await fake.disconnect()  # should not raise
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fake_voice.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.fake_voice'`

**Step 3: Write the implementation**

Create `tests/fake_voice.py`:

```python
"""Fake voice client for E2E testing without Discord."""

from __future__ import annotations

import numpy as np

# C major chord frequencies — same as test_e2e_multi_speaker.py
CHORD_FREQS = [261.63, 329.63, 392.00, 523.25, 659.25]
SAMPLE_RATE = 48000
FRAME_SAMPLES = 960  # 20ms at 48kHz
AMPLITUDE = 6000


def sine_tone_speakers(
    n_ticks: int,
    *,
    freqs: list[float] | None = None,
    amplitude: int = AMPLITUDE,
) -> dict[int, list[np.ndarray]]:
    """Generate per-speaker sine tone frames for FakeVoiceRecvClient.

    Returns {user_id: [frame0, frame1, ...]} where each frame is 960 mono int16 samples.
    """
    freqs = freqs or CHORD_FREQS
    speakers: dict[int, list[np.ndarray]] = {}
    for i, freq in enumerate(freqs):
        user_id = i + 1
        total_samples = n_ticks * FRAME_SAMPLES
        t = np.arange(total_samples, dtype=np.float64) / SAMPLE_RATE
        wave = (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.int16)
        frames = [wave[j * FRAME_SAMPLES : (j + 1) * FRAME_SAMPLES] for j in range(n_ticks)]
        speakers[user_id] = frames
    return speakers


class FakeVoiceRecvClient:
    """Test double for VoiceRecvClient that produces deterministic audio."""

    def __init__(self, speakers: dict[int, list[np.ndarray]]) -> None:
        self._speakers = speakers
        self._tick_idx = 0
        self._listening = False
        self._max_ticks = max((len(f) for f in speakers.values()), default=0)

    def play(self, source: object) -> None:
        pass  # Phone→Discord direction not under test

    def start_listening(self) -> None:
        self._listening = True

    def stop_listening(self) -> None:
        self._listening = False

    def stop(self) -> None:
        self._listening = False

    def pop_tick(self) -> dict[int, np.ndarray]:
        if not self._listening or self._tick_idx >= self._max_ticks:
            return {}
        tick: dict[int, np.ndarray] = {}
        for user_id, frames in self._speakers.items():
            if self._tick_idx < len(frames):
                tick[user_id] = frames[self._tick_idx]
        self._tick_idx += 1
        return tick

    def is_connected(self) -> bool:
        return True

    async def disconnect(self, *, force: bool = False) -> None:
        pass
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fake_voice.py -v`
Expected: All 7 tests PASS

**Step 5: Run full checks**

Run: `just`
Expected: All pass

**Step 6: Commit**

```
test: add FakeVoiceRecvClient for E2E harness

Deterministic voice client fake that produces sine-tone audio for
5 speakers. Used by the E2E test harness to replace Discord voice.
```

---

### Task 5: Build the E2E bridge test (sine tones)

The main test: SIP INVITE → bridge setup → 5-speaker audio → RTP verification.

**Files:**
- Create: `tests/test_e2e_bridge.py`

**Step 1: Write the test**

Create `tests/test_e2e_bridge.py`:

```python
"""E2E test: SIP INVITE → Discord bridge → 5-speaker RTP audio delivery."""

from __future__ import annotations

import asyncio
from functools import partial

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio

from frizzle_phone.rtp.pcmu import ulaw_to_pcm
from frizzle_phone.sip.message import parse_message
from frizzle_phone.sip.server import start_server

from tests.audio_helpers import pcm_to_wav, wav_samples_check
from tests.fake_voice import FakeVoiceRecvClient, sine_tone_speakers, CHORD_FREQS

# --- Constants ---
NUM_SPEAKERS = 5
TICKS = 200  # 4s of audio
SAMPLE_RATE_8K = 8000
SAMPLES_PER_TICK = 160  # 20ms at 8kHz
TEST_GUILD_ID = 1234
TEST_CHANNEL_ID = 5678


# --- Helpers (reused from test_e2e_sip.py pattern) ---


class _ClientProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait(data)


class _RtpCollector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.packets.append(data)


async def _recv(queue: asyncio.Queue[bytes], timeout: float = 2.0) -> bytes:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def _recv_responses(
    queue: asyncio.Queue[bytes], n: int, timeout: float = 2.0
) -> list[bytes]:
    return [await _recv(queue, timeout=timeout) for _ in range(n)]


class _FakeVoiceConnector:
    """Returns a pre-built FakeVoiceRecvClient for the test guild/channel."""

    def __init__(self, fake_vc: FakeVoiceRecvClient) -> None:
        self._fake_vc = fake_vc

    async def connect(
        self, guild_id: int, channel_id: int
    ) -> FakeVoiceRecvClient:
        return self._fake_vc


def _build_invite(
    server_port: int,
    client_port: int,
    rtp_port: int,
    *,
    call_id: str = "e2e-bridge",
    branch: str = "z9hG4bKbr1",
) -> bytes:
    sdp = (
        "v=0\r\n"
        "o=test 0 0 IN IP4 127.0.0.1\r\n"
        "s=test\r\n"
        "c=IN IP4 127.0.0.1\r\n"
        "t=0 0\r\n"
        f"m=audio {rtp_port} RTP/AVP 0\r\n"
    )
    body = sdp.encode()
    lines = [
        f"INVITE sip:discord@127.0.0.1:{server_port} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch={branch}",
        "From: <sip:test@127.0.0.1>;tag=fromtag1",
        "To: <sip:discord@127.0.0.1>",
        f"Call-ID: {call_id}",
        "CSeq: 1 INVITE",
        f"Contact: <sip:test@127.0.0.1:{client_port}>",
        "Max-Forwards: 70",
        "Content-Type: application/sdp",
        f"Content-Length: {len(body)}",
        "",
        "",
    ]
    return "\r\n".join(lines).encode() + body


def _build_ack(
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-bridge",
    branch: str = "z9hG4bKbr2",
) -> bytes:
    lines = [
        f"ACK sip:frizzle@127.0.0.1:{server_port} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch={branch}",
        "From: <sip:test@127.0.0.1>;tag=fromtag1",
        "To: <sip:discord@127.0.0.1>",
        f"Call-ID: {call_id}",
        "CSeq: 1 ACK",
        f"Contact: <sip:test@127.0.0.1:{client_port}>",
        "Max-Forwards: 70",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


def _build_bye(
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-bridge",
    branch: str = "z9hG4bKbr3",
) -> bytes:
    lines = [
        f"BYE sip:frizzle@127.0.0.1:{server_port} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch={branch}",
        "From: <sip:test@127.0.0.1>;tag=fromtag1",
        "To: <sip:discord@127.0.0.1>",
        f"Call-ID: {call_id}",
        "CSeq: 2 BYE",
        f"Contact: <sip:test@127.0.0.1:{client_port}>",
        "Max-Forwards: 70",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


def _parse_rtp_payload(data: bytes) -> bytes:
    return data[12:] if len(data) > 12 else b""


# --- Fixtures ---


@pytest_asyncio.fixture
async def discord_db(db: aiosqlite.Connection) -> aiosqlite.Connection:
    """DB seeded with a discord extension pointing to test guild/channel."""
    await db.execute(
        "INSERT INTO discord_extensions (extension, guild_id, channel_id)"
        " VALUES ('discord', ?, ?)",
        (TEST_GUILD_ID, TEST_CHANNEL_ID),
    )
    await db.commit()
    return db


# --- Tests ---


@pytest.mark.asyncio
async def test_five_speaker_bridge_audio(discord_db: aiosqlite.Connection, file_regression) -> None:
    """Full E2E: INVITE → bridge with 5 sine-tone speakers → verify RTP audio."""
    loop = asyncio.get_running_loop()

    # 1. Set up RTP collector ("the phone")
    collector = _RtpCollector()
    rtp_recv_transport, _ = await loop.create_datagram_endpoint(
        lambda: collector, local_addr=("127.0.0.1", 0)
    )
    rtp_port = rtp_recv_transport.get_extra_info("sockname")[1]

    # 2. Set up fake voice client with 5 speakers
    speakers = sine_tone_speakers(n_ticks=TICKS)
    fake_vc = FakeVoiceRecvClient(speakers)
    connector = _FakeVoiceConnector(fake_vc)

    # 3. Start SIP server with fake voice connector
    from unittest.mock import MagicMock

    sip_transport, server = await start_server(
        "127.0.0.1",
        0,
        server_ip="127.0.0.1",
        db=discord_db,
        audio_buffers={},
        bot=MagicMock(),
        voice_connector=connector,
    )
    _, server_port = sip_transport.get_extra_info("sockname")

    # 4. SIP client
    sip_proto = _ClientProtocol()
    sip_client_transport, _ = await loop.create_datagram_endpoint(
        lambda: sip_proto, remote_addr=("127.0.0.1", server_port)
    )
    client_port = sip_client_transport.get_extra_info("sockname")[1]

    try:
        # 5. INVITE with SDP pointing to our RTP collector
        sip_client_transport.sendto(
            _build_invite(server_port, client_port, rtp_port)
        )

        # 6. Expect 100 Trying + 200 OK
        responses = await _recv_responses(sip_proto.queue, 2, timeout=5.0)
        trying = parse_message(responses[0])
        assert trying.uri == "100"
        ok = parse_message(responses[1])
        assert ok.uri == "200"
        assert ok.body and "m=audio" in ok.body

        # 7. ACK → triggers bridge setup
        sip_client_transport.sendto(
            _build_ack(server_port, client_port)
        )

        # 8. Wait for RTP packets (~4s for 200 ticks at 20ms pacing)
        for _ in range(2500):
            await asyncio.sleep(0.01)
            if len(collector.packets) >= TICKS:
                break

        assert len(collector.packets) >= TICKS, (
            f"Only received {len(collector.packets)}/{TICKS} RTP packets"
        )

        # 9. Verify audio content via golden file
        received_ulaw = b""
        for pkt in collector.packets[:TICKS]:
            received_ulaw += _parse_rtp_payload(pkt)

        pcm_8k = ulaw_to_pcm(received_ulaw)
        wav_bytes = pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)

        check_fn = partial(wav_samples_check, max_rmse=30.0, min_correlation=0.999)
        file_regression.check(
            wav_bytes, binary=True, extension=".wav", check_fn=check_fn
        )

        # 10. Verify FFT peaks at chord frequencies
        pcm_arr = np.frombuffer(pcm_8k, dtype=np.int16).astype(np.float64)
        fft_mag = np.abs(np.fft.rfft(pcm_arr))
        freqs = np.fft.rfftfreq(len(pcm_arr), d=1.0 / SAMPLE_RATE_8K)

        for tone_freq in CHORD_FREQS:
            if tone_freq >= SAMPLE_RATE_8K / 2:
                continue  # Above Nyquist for 8kHz
            idx = np.argmin(np.abs(freqs - tone_freq))
            # Check that there's a peak near this frequency (within 5 bins)
            local_max = np.max(fft_mag[max(0, idx - 5) : idx + 6])
            assert local_max > fft_mag.mean() * 5, (
                f"No FFT peak near {tone_freq}Hz (local_max={local_max:.0f}, "
                f"mean={fft_mag.mean():.0f})"
            )

        # 11. BYE
        sip_client_transport.sendto(
            _build_bye(server_port, client_port)
        )
        bye_resp = parse_message(await _recv(sip_proto.queue))
        assert bye_resp.uri == "200"

    finally:
        sip_client_transport.close()
        sip_transport.close()
        rtp_recv_transport.close()
```

**Step 2: Run test to verify it works**

Run: `uv run pytest tests/test_e2e_bridge.py -v --timeout=30`

Expected: On first run, `file_regression` creates the golden `.wav` file. Test passes.

Note: This test takes ~4-5 seconds due to real 20ms RTP pacing. This is expected.

**Step 3: Run full checks**

Run: `just`
Expected: All pass

**Step 4: Commit**

```
test: add E2E bridge test with 5 simulated Discord speakers

Full pipeline test: SIP INVITE → VoiceConnector → BridgeManager →
rtp_send_loop → RTP/UDP → PCMU decode → FFT verification + golden
file regression. Exercises the real SIP state machine, bridge wiring,
audio mixing, AGC, resampling, and RTP delivery over UDP.
```

---

### Task 6: Generate golden WAV file

The first test run with `--force-regen` creates the golden file. This must be done inside the devcontainer for reproducibility.

**Step 1: Generate golden file inside devcontainer**

Run: `just up` (if not already running)
Run: `uv run pytest tests/test_e2e_bridge.py -v --force-regen --timeout=30`

**Step 2: Verify golden file was created**

Run: `ls -la tests/test_e2e_bridge/`
Expected: `test_five_speaker_bridge_audio.wav` exists

**Step 3: Run again without --force-regen to verify it matches**

Run: `uv run pytest tests/test_e2e_bridge.py -v --timeout=30`
Expected: PASS (golden file comparison succeeds)

**Step 4: Commit golden file**

```
test: add golden WAV for 5-speaker bridge E2E test
```

---

### Task 7: Verify all existing tests still pass

Run the full test suite and all CI checks to ensure nothing was broken.

**Step 1: Run full suite**

Run: `just`
Expected: All lint, format, types, vulture checks pass

Run: `just test`
Expected: All 244+ tests pass (plus our new ones)

**Step 2: Verify vulture doesn't flag new code**

Run: `uv run vulture`
Expected: No new false positives. If vulture flags protocol methods, add them to the vulture allowlist.

**Step 3: Commit any fixups**

If any adjustments were needed (vulture allowlist, import cleanup), commit them:

```
chore: fixups for E2E harness CI compliance
```
