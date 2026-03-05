# discord_voice_rx Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the external `discord-ext-voice-recv` dependency with an in-house `discord_voice_rx` module that receives Discord voice audio and exposes a `pop_tick()` API for the RTP send loop.

**Architecture:** Socket callback (discord.py's reader thread) decrypts packets and pushes to a queue. A single decoder thread does opus decode + per-user buffering. The asyncio RTP send loop calls `pop_tick()` to pull synchronized per-user PCM frames. Gateway hook maps SSRCs to user IDs.

**Tech Stack:** discord.py, pynacl, davey, discord.opus (libopus), numpy

**Design doc:** `docs/plans/2026-03-05-discord-voice-rx-design.md`

**Upstream reference:** `/tmp/discord-ext-voice-recv/` (cloned for reference)

---

## Task 1: RTP Packet Parsing (`rtp.py`)

Parses raw RTP/RTCP packets from Discord's voice UDP socket. Ported from upstream `rtp.py`, stripped to what we need.

**Files:**
- Create: `src/frizzle_phone/discord_voice_rx/__init__.py` (empty for now)
- Create: `src/frizzle_phone/discord_voice_rx/rtp.py`
- Create: `tests/test_voice_rx_rtp.py`

**Step 1: Write the failing tests**

```python
# tests/test_voice_rx_rtp.py
"""Tests for discord_voice_rx RTP packet parsing."""

import struct

import pytest

from frizzle_phone.discord_voice_rx.rtp import (
    OPUS_SILENCE,
    RtpPacket,
    is_rtcp,
    parse_rtp,
)


def _build_rtp(
    *,
    ssrc: int = 1234,
    seq: int = 100,
    timestamp: int = 48000,
    payload: bytes = b"\xaa" * 20,
    marker: bool = False,
    extended: bool = False,
) -> bytes:
    """Build a minimal RTP packet for testing."""
    flags = 0x80  # version=2
    if extended:
        flags |= 0x10
    pt = 0x60 | (0x80 if marker else 0x00)  # payload type 96, marker bit
    header = struct.pack(">BBHII", flags, pt, seq, timestamp, ssrc)
    return header + payload


class TestIsRtcp:
    def test_rtcp_sender_report(self):
        data = bytes([0x80, 200]) + b"\x00" * 10
        assert is_rtcp(data) is True

    def test_rtcp_receiver_report(self):
        data = bytes([0x80, 201]) + b"\x00" * 10
        assert is_rtcp(data) is True

    def test_rtp_not_rtcp(self):
        data = _build_rtp()
        assert is_rtcp(data) is False

    def test_boundary_199_not_rtcp(self):
        data = bytes([0x80, 199]) + b"\x00" * 10
        assert is_rtcp(data) is False

    def test_boundary_205_not_rtcp(self):
        data = bytes([0x80, 205]) + b"\x00" * 10
        assert is_rtcp(data) is False


class TestParseRtp:
    def test_basic_fields(self):
        pkt = parse_rtp(_build_rtp(ssrc=5678, seq=42, timestamp=96000))
        assert pkt.ssrc == 5678
        assert pkt.sequence == 42
        assert pkt.timestamp == 96000
        assert pkt.version == 2

    def test_marker_bit(self):
        pkt = parse_rtp(_build_rtp(marker=True))
        assert pkt.marker is True

    def test_payload_data(self):
        payload = b"\xde\xad\xbe\xef"
        pkt = parse_rtp(_build_rtp(payload=payload))
        assert pkt.data == bytearray(payload)

    def test_header_is_12_bytes(self):
        pkt = parse_rtp(_build_rtp())
        assert len(pkt.header) == 12

    def test_extended_flag(self):
        pkt = parse_rtp(_build_rtp(extended=True))
        assert pkt.extended is True

    def test_silence_detection(self):
        pkt = parse_rtp(_build_rtp(payload=OPUS_SILENCE))
        # Set decrypted_data to opus silence to test is_silence
        pkt.decrypted_data = OPUS_SILENCE
        assert pkt.is_silence() is True

    def test_non_silence(self):
        pkt = parse_rtp(_build_rtp(payload=b"\x01\x02\x03"))
        pkt.decrypted_data = b"\x01\x02\x03"
        assert pkt.is_silence() is False

    def test_no_decrypted_data_not_silence(self):
        pkt = parse_rtp(_build_rtp())
        assert pkt.is_silence() is False

    def test_comparison_by_sequence(self):
        pkt_a = parse_rtp(_build_rtp(ssrc=1, seq=10, timestamp=100))
        pkt_b = parse_rtp(_build_rtp(ssrc=1, seq=20, timestamp=200))
        assert pkt_a < pkt_b

    def test_adjust_rtpsize(self):
        """rtpsize mode: 4-byte nonce at end of data, ext header moved to header."""
        inner_payload = b"\xcc" * 10
        nonce = b"\x01\x02\x03\x04"
        pkt = parse_rtp(_build_rtp(payload=inner_payload + nonce, extended=True))
        pkt.adjust_rtpsize()
        assert pkt.nonce == bytearray(nonce)
```

**Step 2: Run tests to verify they fail**

Run: `just test -- tests/test_voice_rx_rtp.py -v`
Expected: FAIL — module not found

**Step 3: Create `__init__.py` and implement `rtp.py`**

Create `src/frizzle_phone/discord_voice_rx/__init__.py` — empty file.

Create `src/frizzle_phone/discord_voice_rx/rtp.py`:

Port from upstream `/tmp/discord-ext-voice-recv/discord/ext/voice_recv/rtp.py`. Keep:
- `OPUS_SILENCE` constant
- `is_rtcp()` function
- `parse_rtp()` function (renamed from `decode_rtp`)
- `RtpPacket` class with: version, padding, extended, cc, marker, payload type, sequence, timestamp, ssrc, header, data, decrypted_data, nonce, `_rtpsize`, `is_silence()`, `adjust_rtpsize()`, `update_ext_headers()`, `_parse_bede_header()`, comparison methods (`__lt__`, `__gt__`, `__eq__`)
- `FakePacket` class (for FEC gap filling)

Strip out:
- All RTCP packet classes (SenderReportPacket, ReceiverReportPacket, SDESPacket, BYEPacket, APPPacket) — we only need to detect RTCP, not parse it
- `SilencePacket` class
- `decode_rtcp()`, `decode()` functions
- `_rtcp_map` dict
- `ExtensionID` class

**Step 4: Run tests to verify they pass**

Run: `just test -- tests/test_voice_rx_rtp.py -v`
Expected: PASS

**Step 5: Commit**

```
git add src/frizzle_phone/discord_voice_rx/__init__.py src/frizzle_phone/discord_voice_rx/rtp.py tests/test_voice_rx_rtp.py
git commit -m "Add RTP packet parsing for discord_voice_rx"
```

---

## Task 2: Packet Decryptor (`decrypt.py`)

Decrypts RTP packets using nacl (4 encryption modes) + DAVE decryption.

**Files:**
- Create: `src/frizzle_phone/discord_voice_rx/decrypt.py`
- Create: `tests/test_voice_rx_decrypt.py`

**Step 1: Write the failing tests**

```python
# tests/test_voice_rx_decrypt.py
"""Tests for discord_voice_rx packet decryption."""

from unittest.mock import MagicMock

import nacl.secret
import nacl.utils
import pytest

from frizzle_phone.discord_voice_rx.decrypt import PacketDecryptor, dave_decrypt
from frizzle_phone.discord_voice_rx.rtp import parse_rtp


def _make_secret_key() -> bytes:
    """Generate a valid 32-byte secret key."""
    return nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)


class TestPacketDecryptorModes:
    """Test that all 4 encryption modes can round-trip encrypt/decrypt."""

    def test_xsalsa20_poly1305(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305", key)
        assert decryptor.mode == "xsalsa20_poly1305"

    def test_xsalsa20_poly1305_lite(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305_lite", key)
        assert decryptor.mode == "xsalsa20_poly1305_lite"

    def test_xsalsa20_poly1305_suffix(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305_suffix", key)
        assert decryptor.mode == "xsalsa20_poly1305_suffix"

    def test_aead_xchacha20_poly1305_rtpsize(self):
        key = _make_secret_key()
        decryptor = PacketDecryptor("aead_xchacha20_poly1305_rtpsize", key)
        assert decryptor.mode == "aead_xchacha20_poly1305_rtpsize"

    def test_unsupported_mode_raises(self):
        key = _make_secret_key()
        with pytest.raises(NotImplementedError):
            PacketDecryptor("fake_mode", key)

    def test_update_secret_key(self):
        key1 = _make_secret_key()
        key2 = _make_secret_key()
        decryptor = PacketDecryptor("xsalsa20_poly1305", key1)
        old_box = decryptor._box
        decryptor.update_secret_key(key2)
        assert decryptor._box is not old_box


class TestDaveDecrypt:
    def test_dave_decrypts_when_ready(self):
        dave_session = MagicMock()
        dave_session.ready = True
        dave_session.decrypt.return_value = b"decrypted_payload"

        result = dave_decrypt(
            dave_session=dave_session,
            ssrc_to_id={1234: 42},
            ssrc=1234,
            transport_decrypted=b"encrypted_payload",
        )
        assert result == b"decrypted_payload"
        dave_session.decrypt.assert_called_once()

    def test_dave_not_ready_passthrough(self):
        dave_session = MagicMock()
        dave_session.ready = False

        result = dave_decrypt(
            dave_session=dave_session,
            ssrc_to_id={1234: 42},
            ssrc=1234,
            transport_decrypted=b"original",
        )
        assert result == b"original"
        dave_session.decrypt.assert_not_called()

    def test_dave_none_passthrough(self):
        result = dave_decrypt(
            dave_session=None,
            ssrc_to_id={1234: 42},
            ssrc=1234,
            transport_decrypted=b"original",
        )
        assert result == b"original"

    def test_dave_unknown_ssrc_passthrough(self):
        dave_session = MagicMock()
        dave_session.ready = True

        result = dave_decrypt(
            dave_session=dave_session,
            ssrc_to_id={},
            ssrc=9999,
            transport_decrypted=b"original",
        )
        assert result == b"original"
        dave_session.decrypt.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `just test -- tests/test_voice_rx_decrypt.py -v`
Expected: FAIL — module not found

**Step 3: Implement `decrypt.py`**

Port `PacketDecryptor` from upstream `reader.py:189-297`. Add `dave_decrypt()` as a standalone function (extracted from the monkey-patched callback). The decryptor methods should decrypt RTP packets and return the decrypted payload bytes.

Reference for all 4 modes: `/tmp/discord-ext-voice-recv/discord/ext/voice_recv/reader.py:216-297`
Reference for DAVE decrypt: `src/frizzle_phone/discord_patches.py:54-62`

**Step 4: Run tests to verify they pass**

Run: `just test -- tests/test_voice_rx_decrypt.py -v`
Expected: PASS

**Step 5: Commit**

```
git add src/frizzle_phone/discord_voice_rx/decrypt.py tests/test_voice_rx_decrypt.py
git commit -m "Add packet decryption (4 nacl modes + DAVE) for discord_voice_rx"
```

---

## Task 3: VoiceRecvStats (`stats.py`)

Metrics for monitoring voice receive performance. Matches `BridgeStats` pattern.

**Files:**
- Create: `src/frizzle_phone/discord_voice_rx/stats.py`
- Create: `tests/test_voice_rx_stats.py`

**Step 1: Write the failing tests**

```python
# tests/test_voice_rx_stats.py
"""Tests for VoiceRecvStats."""

import logging

from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats


def test_initial_counters_zero():
    stats = VoiceRecvStats()
    assert stats.packets_in == 0
    assert stats.opus_decodes == 0
    assert stats.ticks_empty == 0
    assert stats.max_callback_us == 0


def test_reset_clears_counters():
    stats = VoiceRecvStats()
    stats.packets_in = 100
    stats.opus_decodes = 50
    stats.max_decode_us = 500
    stats.reset()
    assert stats.packets_in == 0
    assert stats.opus_decodes == 0
    assert stats.max_decode_us == 0


def test_log_and_reset_emits_log(caplog):
    stats = VoiceRecvStats()
    stats.packets_in = 10
    stats.opus_decodes = 8
    with caplog.at_level(logging.INFO, logger="frizzle_phone.discord_voice_rx.stats"):
        stats.log_and_reset()
    assert any("voice_recv stats" in r.message for r in caplog.records)
    assert stats.packets_in == 0


def test_maybe_log_respects_interval():
    stats = VoiceRecvStats()
    stats.packets_in = 5
    # First call should not log (interval not elapsed)
    stats.maybe_log_and_reset()
    # Counter should still be set (interval hasn't elapsed)
    assert stats.packets_in == 5


def test_decrypt_failed_warning(caplog):
    stats = VoiceRecvStats()
    stats.packets_in = 100
    stats.packets_decrypt_failed = 10
    with caplog.at_level(logging.WARNING, logger="frizzle_phone.discord_voice_rx.stats"):
        stats.log_and_reset()
    assert any("decrypt" in r.message.lower() for r in caplog.records)
```

**Step 2: Run tests to verify they fail**

Run: `just test -- tests/test_voice_rx_stats.py -v`
Expected: FAIL

**Step 3: Implement `stats.py`**

Model after `src/frizzle_phone/bridge_stats.py`. Same pattern: plain int/float fields, `reset()`, `log_and_reset()`, `maybe_log_and_reset()` with 5s interval. Log line prefix: `voice_recv stats`. Include warnings for high decrypt failure rate and high opus error rate.

**Step 4: Run tests to verify they pass**

Run: `just test -- tests/test_voice_rx_stats.py -v`
Expected: PASS

**Step 5: Commit**

```
git add src/frizzle_phone/discord_voice_rx/stats.py tests/test_voice_rx_stats.py
git commit -m "Add VoiceRecvStats metrics for discord_voice_rx"
```

---

## Task 4: Jitter Buffer (`decoder.py` — buffer part)

Per-SSRC heap-based jitter buffer for reordering packets before opus decode.

**Files:**
- Create: `src/frizzle_phone/discord_voice_rx/decoder.py`
- Create: `tests/test_voice_rx_decoder.py`

**Step 1: Write the failing tests**

```python
# tests/test_voice_rx_decoder.py
"""Tests for discord_voice_rx decoder: jitter buffer and decoder thread."""

from unittest.mock import MagicMock

import numpy as np

from frizzle_phone.discord_voice_rx.decoder import JitterBuffer, stereo_to_mono


class TestJitterBuffer:
    def _make_packet(self, ssrc: int = 1, seq: int = 0, ts: int = 0):
        pkt = MagicMock()
        pkt.ssrc = ssrc
        pkt.sequence = seq
        pkt.timestamp = ts
        # Make it sortable for the heap
        pkt.__lt__ = lambda s, o: s.sequence < o.sequence
        pkt.__gt__ = lambda s, o: s.sequence > o.sequence
        pkt.__eq__ = lambda s, o: s.sequence == o.sequence
        pkt.__le__ = lambda s, o: s.sequence <= o.sequence
        pkt.__ge__ = lambda s, o: s.sequence >= o.sequence
        pkt.__bool__ = lambda s: True  # real packet, not fake
        return pkt

    def test_push_and_pop_ordered(self):
        buf = JitterBuffer(prefill=1)
        p1 = self._make_packet(seq=1)
        p2 = self._make_packet(seq=2)
        buf.push(p1)
        buf.push(p2)
        assert buf.pop() is p1
        assert buf.pop() is p2

    def test_reorders_out_of_order(self):
        buf = JitterBuffer(prefill=2)
        p1 = self._make_packet(seq=1)
        p2 = self._make_packet(seq=2)
        p3 = self._make_packet(seq=3)
        buf.push(p3)
        buf.push(p1)
        buf.push(p2)
        assert buf.pop().sequence == 1
        assert buf.pop().sequence == 2
        assert buf.pop().sequence == 3

    def test_pop_empty_returns_none(self):
        buf = JitterBuffer(prefill=1)
        assert buf.pop() is None

    def test_prefill_delays_output(self):
        buf = JitterBuffer(prefill=2)
        p1 = self._make_packet(seq=1)
        buf.push(p1)
        assert buf.pop() is None  # prefill not met yet
        p2 = self._make_packet(seq=2)
        buf.push(p2)
        assert buf.pop() is not None  # prefill met

    def test_overflow_drops_oldest(self):
        buf = JitterBuffer(prefill=0, maxsize=3)
        for i in range(5):
            buf.push(self._make_packet(seq=i))
        # Only 3 should remain (newest)
        assert len(buf) <= 3

    def test_reset_clears(self):
        buf = JitterBuffer(prefill=1)
        buf.push(self._make_packet(seq=1))
        buf.push(self._make_packet(seq=2))
        buf.reset()
        assert buf.pop() is None
        assert len(buf) == 0

    def test_gap_detection(self):
        """After popping seq=1, with seq=3 buffered, gap should be 1."""
        buf = JitterBuffer(prefill=0)
        buf.push(self._make_packet(seq=1))
        buf.push(self._make_packet(seq=3))
        buf.pop()  # pops seq=1
        assert buf.gap() >= 1


class TestStereoToMono:
    def test_halves_length(self):
        stereo = b"\x00" * 3840  # 960 stereo samples
        mono = stereo_to_mono(stereo)
        assert isinstance(mono, np.ndarray)
        assert len(mono) == 960

    def test_averages_channels(self):
        stereo = np.array([100, 200], dtype=np.int16).tobytes()
        mono = stereo_to_mono(stereo)
        assert mono[0] == 150
```

**Step 2: Run tests to verify they fail**

Run: `just test -- tests/test_voice_rx_decoder.py -v`
Expected: FAIL

**Step 3: Implement the jitter buffer and stereo_to_mono**

In `src/frizzle_phone/discord_voice_rx/decoder.py`:

- `JitterBuffer` class: simplified from upstream `buffer.py:HeapJitterBuffer`. Uses `heapq`. Parameters: `maxsize=10`, `prefill=2`. Tracks `_last_seq` for gap detection. Methods: `push()`, `pop()`, `peek_next()`, `gap()`, `reset()`, `__len__()`, `flush()`.
- `stereo_to_mono()` function: moved from `bridge.py:37-43`.
- Stub `DecoderThread` class (will be filled in Task 5).

Reference: `/tmp/discord-ext-voice-recv/discord/ext/voice_recv/buffer.py`
Reference for `stereo_to_mono`: `src/frizzle_phone/bridge.py:37-43`
Reference for `gap_wrapped`/`add_wrapped`: `/tmp/discord-ext-voice-recv/discord/ext/voice_recv/utils.py:20-31`

**Step 4: Run tests to verify they pass**

Run: `just test -- tests/test_voice_rx_decoder.py -v`
Expected: PASS

**Step 5: Commit**

```
git add src/frizzle_phone/discord_voice_rx/decoder.py tests/test_voice_rx_decoder.py
git commit -m "Add jitter buffer and stereo_to_mono for discord_voice_rx"
```

---

## Task 5: Decoder Thread (`decoder.py` — thread part)

The decoder thread consumes decrypted packets, runs opus decode with FEC, converts stereo→mono, and buffers per-user frames. Exposes `pop_tick()`.

**Files:**
- Modify: `src/frizzle_phone/discord_voice_rx/decoder.py`
- Modify: `tests/test_voice_rx_decoder.py`

**Step 1: Write the failing tests**

Add to `tests/test_voice_rx_decoder.py`:

```python
import queue
import threading
import time

from frizzle_phone.discord_voice_rx.decoder import DecoderThread
from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats


class TestDecoderThread:
    def test_pop_tick_empty_when_no_data(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        result = dt.pop_tick()
        assert result == {}

    def test_pop_tick_returns_one_frame_per_user(self):
        """Feed two users' decoded frames, pop_tick returns one per user."""
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        dt.start()
        try:
            # Manually push decoded frames into per-user buffers (bypassing opus)
            mono = np.zeros(960, dtype=np.int16)
            with dt._lock:
                dt._user_buffers[1].append(mono.copy())
                dt._user_buffers[1].append(mono.copy())  # 2 frames for user 1
                dt._user_buffers[2].append(mono.copy())   # 1 frame for user 2

            tick = dt.pop_tick()
            assert set(tick.keys()) == {1, 2}
            assert len(tick[1]) == 960
            assert len(tick[2]) == 960

            # User 1 had 2 frames, so one should remain
            tick2 = dt.pop_tick()
            assert 1 in tick2
            assert 2 not in tick2
        finally:
            dt.stop()

    def test_pop_tick_increments_stats(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        result = dt.pop_tick()
        assert stats.ticks_empty == 1

    def test_stop_terminates_thread(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        dt.start()
        dt.stop()
        dt.join(timeout=2.0)
        assert not dt.is_alive()

    def test_destroy_decoder_clears_user_buffer(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        mono = np.zeros(960, dtype=np.int16)
        with dt._lock:
            dt._user_buffers[42].append(mono)
        dt.destroy_decoder(ssrc=1234, user_id=42)
        tick = dt.pop_tick()
        assert 42 not in tick
```

**Step 2: Run tests to verify they fail**

Run: `just test -- tests/test_voice_rx_decoder.py::TestDecoderThread -v`
Expected: FAIL

**Step 3: Implement `DecoderThread`**

In `decoder.py`, implement `DecoderThread(threading.Thread)`:

- Constructor takes `stats: VoiceRecvStats`
- `_packet_queue: queue.Queue` — receives `(ssrc, user_id, packet)` tuples from socket callback
- `_jitter_buffers: dict[int, JitterBuffer]` — per-SSRC
- `_decoders: dict[int, discord.opus.Decoder]` — per-SSRC opus decoders
- `_user_buffers: dict[int, deque[np.ndarray]]` — per-user_id frame deques (max 50)
- `_ssrc_to_user: dict[int, int]` — SSRC→user_id mapping (set by client on SPEAKING events)
- `_lock: threading.Lock` — protects `_user_buffers`
- `_stop_event: threading.Event`

Methods:
- `feed(ssrc: int, packet: RtpPacket) -> None` — called from socket callback thread. Pushes to `_packet_queue`.
- `set_ssrc_user(ssrc: int, user_id: int) -> None` — update mapping
- `destroy_decoder(ssrc: int, user_id: int | None) -> None` — clean up per-SSRC state and user buffer
- `pop_tick() -> dict[int, np.ndarray]` — acquire lock, pop one frame per user, return dict. Update stats.
- `stop() -> None` — signal thread to exit
- `run()` — thread main loop: get packet from queue, route to jitter buffer, pop ready packets, opus decode with FEC, stereo→mono, append to user buffer.

Reference for opus decode + FEC: `/tmp/discord-ext-voice-recv/discord/ext/voice_recv/opus.py:149-174`

**Step 4: Run tests to verify they pass**

Run: `just test -- tests/test_voice_rx_decoder.py -v`
Expected: PASS

**Step 5: Commit**

```
git add src/frizzle_phone/discord_voice_rx/decoder.py tests/test_voice_rx_decoder.py
git commit -m "Add decoder thread with opus decode, FEC, and pop_tick() API"
```

---

## Task 6: Gateway Hook (`gateway.py`)

Intercepts voice websocket events to map SSRCs to user IDs and clean up on disconnect.

**Files:**
- Create: `src/frizzle_phone/discord_voice_rx/gateway.py`
- Create: `tests/test_voice_rx_gateway.py`

**Step 1: Write the failing tests**

```python
# tests/test_voice_rx_gateway.py
"""Tests for discord_voice_rx gateway hook."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from frizzle_phone.discord_voice_rx.gateway import hook

# Voice WS opcode constants
READY = 2
SESSION_DESCRIPTION = 4
SPEAKING = 5
CLIENT_DISCONNECT = 13


@pytest.fixture
def voice_client():
    vc = MagicMock()
    vc._ssrc_to_id = {}
    vc._id_to_ssrc = {}
    vc._decoder_thread = MagicMock()
    vc.guild.me.id = 999
    return vc


@pytest.fixture
def ws(voice_client):
    ws = MagicMock()
    ws._connection.voice_client = voice_client
    ws.secret_key = b"\x00" * 32
    ws.READY = READY
    ws.SESSION_DESCRIPTION = SESSION_DESCRIPTION
    return ws


@pytest.mark.asyncio
async def test_ready_maps_own_ssrc(ws, voice_client):
    msg = {"op": READY, "d": {"ssrc": 12345}}
    await hook(ws, msg)
    assert voice_client._ssrc_to_id[12345] == 999
    assert voice_client._id_to_ssrc[999] == 12345


@pytest.mark.asyncio
async def test_speaking_maps_user_ssrc(ws, voice_client):
    msg = {"op": SPEAKING, "d": {"user_id": "42", "ssrc": 5678, "speaking": 1}}
    await hook(ws, msg)
    assert voice_client._ssrc_to_id[5678] == 42
    assert voice_client._id_to_ssrc[42] == 5678
    voice_client._decoder_thread.set_ssrc_user.assert_called_with(5678, 42)


@pytest.mark.asyncio
async def test_client_disconnect_removes_ssrc(ws, voice_client):
    voice_client._ssrc_to_id = {5678: 42}
    voice_client._id_to_ssrc = {42: 5678}
    msg = {"op": CLIENT_DISCONNECT, "d": {"user_id": "42"}}
    await hook(ws, msg)
    assert 5678 not in voice_client._ssrc_to_id
    assert 42 not in voice_client._id_to_ssrc
    voice_client._decoder_thread.destroy_decoder.assert_called_with(ssrc=5678, user_id=42)


@pytest.mark.asyncio
async def test_client_disconnect_unknown_user_noop(ws, voice_client):
    """Disconnect for unknown user should not crash."""
    msg = {"op": CLIENT_DISCONNECT, "d": {"user_id": "999"}}
    await hook(ws, msg)  # should not raise


@pytest.mark.asyncio
async def test_session_description_updates_key(ws, voice_client):
    voice_client._socket_callback = MagicMock()
    msg = {"op": SESSION_DESCRIPTION, "d": {"secret_key": [1, 2, 3]}}
    ws.secret_key = bytes([1, 2, 3] + [0] * 29)
    await hook(ws, msg)
    voice_client._update_secret_key.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `just test -- tests/test_voice_rx_gateway.py -v`
Expected: FAIL

**Step 3: Implement `gateway.py`**

Simple async hook function. Handles opcodes:
- READY (2): map own SSRC → `guild.me.id`
- SESSION_DESCRIPTION (4): update decryptor secret key
- SPEAKING (5): map SSRC → user_id, notify decoder thread
- CLIENT_DISCONNECT (13): remove SSRC mapping, destroy decoder

Reference: `/tmp/discord-ext-voice-recv/discord/ext/voice_recv/gateway.py`

**Step 4: Run tests to verify they pass**

Run: `just test -- tests/test_voice_rx_gateway.py -v`
Expected: PASS

**Step 5: Commit**

```
git add src/frizzle_phone/discord_voice_rx/gateway.py tests/test_voice_rx_gateway.py
git commit -m "Add gateway hook (SPEAKING, CLIENT_DISCONNECT) for discord_voice_rx"
```

---

## Task 7: VoiceRecvClient (`client.py`)

The main entry point. Extends `discord.VoiceClient`, wires up all components.

**Files:**
- Create: `src/frizzle_phone/discord_voice_rx/client.py`
- Modify: `src/frizzle_phone/discord_voice_rx/__init__.py`
- Create: `tests/test_voice_rx_client.py`

**Step 1: Write the failing tests**

```python
# tests/test_voice_rx_client.py
"""Tests for VoiceRecvClient."""

from unittest.mock import MagicMock, patch

from frizzle_phone.discord_voice_rx.client import VoiceRecvClient


class TestVoiceRecvClient:
    def _make_client(self):
        """Create a VoiceRecvClient with mocked discord internals."""
        with patch.object(VoiceRecvClient, "__init__", lambda self, *a, **kw: None):
            vc = VoiceRecvClient.__new__(VoiceRecvClient)
            vc._ssrc_to_id = {}
            vc._id_to_ssrc = {}
            vc._decoder_thread = None
            vc._socket_callback = None
            vc._decryptor = None
            vc._connection = MagicMock()
            vc.secret_key = b"\x00" * 32
            vc.mode = "xsalsa20_poly1305"
            return vc

    def test_add_ssrc(self):
        vc = self._make_client()
        vc._add_ssrc(42, 5678)
        assert vc._ssrc_to_id[5678] == 42
        assert vc._id_to_ssrc[42] == 5678

    def test_remove_ssrc(self):
        vc = self._make_client()
        vc._add_ssrc(42, 5678)
        vc._remove_ssrc(user_id=42)
        assert 5678 not in vc._ssrc_to_id
        assert 42 not in vc._id_to_ssrc

    def test_pop_tick_delegates_to_decoder_thread(self):
        vc = self._make_client()
        vc._decoder_thread = MagicMock()
        vc._decoder_thread.pop_tick.return_value = {1: "frame"}
        assert vc.pop_tick() == {1: "frame"}

    def test_pop_tick_returns_empty_when_not_listening(self):
        vc = self._make_client()
        assert vc.pop_tick() == {}
```

**Step 2: Run tests to verify they fail**

Run: `just test -- tests/test_voice_rx_client.py -v`
Expected: FAIL

**Step 3: Implement `client.py`**

`VoiceRecvClient(discord.VoiceClient)`:
- `__init__`: init `_ssrc_to_id`, `_id_to_ssrc`, `_decoder_thread = None`, `_socket_callback = None`, `_decryptor = None`, `_recv_stats = VoiceRecvStats()`
- `create_connection_state()`: return `VoiceConnectionState(self, hook=hook)` — same pattern as upstream
- `start_listening()`: create `PacketDecryptor`, create `DecoderThread`, register socket callback via `_connection.add_socket_listener()`, start decoder thread
- `stop_listening()`: remove socket listener, stop decoder thread
- `stop()`: stop playing + stop listening
- `pop_tick()`: delegate to `_decoder_thread.pop_tick()` if listening, else empty dict
- `_add_ssrc()`, `_remove_ssrc()`: maintain bidirectional SSRC↔user_id mapping
- `_update_secret_key()`: update decryptor
- `_socket_callback_fn()`: the registered callback — parse RTP, decrypt, filter silence/unknown SSRC, feed to decoder thread. Record timing in stats. This is the function that replaces the monkey-patched `_patched_callback`.
- `recv_stats` property

Update `__init__.py`:
```python
from frizzle_phone.discord_voice_rx.client import VoiceRecvClient
from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats

__all__ = ["VoiceRecvClient", "VoiceRecvStats"]
```

Reference: `/tmp/discord-ext-voice-recv/discord/ext/voice_recv/voice_client.py`
Reference: `src/frizzle_phone/discord_patches.py:41-110` (socket callback logic)

**Step 4: Run tests to verify they pass**

Run: `just test -- tests/test_voice_rx_client.py -v`
Expected: PASS

**Step 5: Run ALL voice_rx tests**

Run: `just test -- tests/test_voice_rx_*.py -v`
Expected: ALL PASS

**Step 6: Commit**

```
git add src/frizzle_phone/discord_voice_rx/client.py src/frizzle_phone/discord_voice_rx/__init__.py tests/test_voice_rx_client.py
git commit -m "Add VoiceRecvClient wiring all discord_voice_rx components"
```

---

## Task 8: Integrate into bridge.py and bridge_manager.py

Replace `PhoneAudioSink` + `drain()` with `pop_tick()`. Delete sink class.

**Files:**
- Modify: `src/frizzle_phone/bridge.py`
- Modify: `src/frizzle_phone/bridge_manager.py`
- Modify: `tests/test_bridge.py`
- Modify: `tests/test_e2e_audio.py`
- Modify: `tests/test_e2e_multi_speaker.py`

**Step 1: Modify `rtp_send_loop` signature**

In `src/frizzle_phone/bridge.py`:
- Remove `from discord.ext import voice_recv` import
- Remove `PhoneAudioSink` class entirely
- Remove `stereo_to_mono()` (moved to decoder.py)
- Change `rtp_send_loop` signature: replace `sink: PhoneAudioSink` with `voice_client: VoiceRecvClient`
  - The `voice_client` parameter is imported from `frizzle_phone.discord_voice_rx`
  - But to avoid circular imports and keep testability, accept a protocol/duck-type: anything with `pop_tick() -> dict[int, ndarray]`

- Replace the drain + slot detection block (lines 203-219) with:
  ```python
  tick = voice_client.pop_tick()
  if tick:
      slot_queue.append(tick)
      while len(slot_queue) > MAX_SLOT_QUEUE:
          slot_queue.popleft()
          if stats:
              stats.d2p_frames_dropped += 1
  ```

The rest of the send loop (slot consumption, AGC, mix, resample, RTP send) stays the same — `pop_tick()` returns a `dict[int, ndarray]` which is exactly what a slot is.

**Step 2: Modify `bridge_manager.py`**

- Replace `from discord.ext import voice_recv` with `from frizzle_phone.discord_voice_rx import VoiceRecvClient`
- Replace `voice_recv.VoiceRecvClient` type hints with `VoiceRecvClient`
- Replace `voice_client.listen(sink)` with `voice_client.start_listening()`
- Remove `sink` from `BridgeHandle.__init__` and `BridgeHandle.stop()`
- Pass `voice_client` to `rtp_send_loop` instead of `sink`
- In `BridgeHandle.stop()`: call `voice_client.stop_listening()` instead of `sink.cleanup()`

**Step 3: Update tests**

Update `tests/test_bridge.py`:
- Remove tests for `PhoneAudioSink` (wants_opus, drain, write, cleanup) — these no longer exist
- Keep tests for `PhoneAudioSource`, `mix_slot`, `ChunkedResampler`
- Remove DAVE callback tests (moved to voice_rx tests)
- Remove `_make_reader` helper

Update `tests/test_e2e_audio.py`:
- Replace `_PacedSink` with a mock object that has `pop_tick()` returning pre-computed dicts
- Remove dependency on `_patched_callback` — the e2e test should test from mono frames through RTP, not from encrypted packets (that's covered by voice_rx unit tests)

Update `tests/test_e2e_multi_speaker.py`:
- Replace `_MultiPacedSink` and `_BurstyMultiPacedSink` with mock objects providing `pop_tick()`
- The tick data format changes: instead of `list[tuple[int, ndarray]]` per drain, each tick is `dict[int, ndarray]`

**Step 4: Run tests**

Run: `just test -v`
Expected: ALL PASS

**Step 5: Commit**

```
git add src/frizzle_phone/bridge.py src/frizzle_phone/bridge_manager.py tests/test_bridge.py tests/test_e2e_audio.py tests/test_e2e_multi_speaker.py
git commit -m "Integrate discord_voice_rx pop_tick() into bridge and bridge_manager"
```

---

## Task 9: Update sip/server.py and __main__.py

Switch imports and remove discord_patches.

**Files:**
- Modify: `src/frizzle_phone/sip/server.py`
- Modify: `src/frizzle_phone/__main__.py`
- Delete: `src/frizzle_phone/discord_patches.py`
- Delete: `tests/test_discord_patches.py`
- Modify: `vulture_whitelist.py`

**Step 1: Update `sip/server.py`**

- Replace `from discord.ext import commands, voice_recv` with `from discord.ext import commands` and `from frizzle_phone.discord_voice_rx import VoiceRecvClient`
- Replace `voice_recv.VoiceRecvClient` with `VoiceRecvClient` (in `PendingBridge`, `DiscordBridgeContext`, `channel.connect(cls=...)`)

**Step 2: Update `__main__.py`**

- Remove `from frizzle_phone.discord_patches import apply_discord_patches`
- Remove `apply_discord_patches()` call (line 34)

**Step 3: Delete old files**

- Delete `src/frizzle_phone/discord_patches.py`
- Delete `tests/test_discord_patches.py`

**Step 4: Update `vulture_whitelist.py`**

Remove the entries that referenced monkey-patched attributes:
- `_._do_run` (line 7)
- `_.callback` (line 10)
- `_.decrypted_data` (line 13)

Keep:
- `_.id` (still used in templates)
- `_.return_value`, `_.side_effect` (still used in mocks)
- `_.__class__` (still used in tests)

**Step 5: Run full checks**

Run: `just` (runs lint, format, types, vulture)
Run: `just test`
Expected: ALL PASS

**Step 6: Commit**

```
git add -A
git commit -m "Switch to discord_voice_rx, remove discord-ext-voice-recv dependency"
```

---

## Task 10: Remove discord-ext-voice-recv dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Remove from pyproject.toml**

Remove line 12: `"discord-ext-voice-recv==0.5.2a179",`

**Step 2: Lock and verify**

Run: `uv lock`
Run: `uv sync`

**Step 3: Run full test suite**

Run: `just` (all checks)
Run: `just test`
Expected: ALL PASS — nothing imports from `discord.ext.voice_recv` anymore

**Step 4: Commit**

```
git add pyproject.toml uv.lock
git commit -m "Remove discord-ext-voice-recv dependency from pyproject.toml"
```

---

## Task 11: Update DESIGN.md

Update the architecture documentation to reflect the new module.

**Files:**
- Modify: `DESIGN.md`

**Step 1: Update DESIGN.md**

Key changes:
- Update the "Discord Bot" section to mention `discord_voice_rx` instead of `discord-ext-voice-recv`
- Update the "Discord→Phone Audio Pipeline" diagram: remove the `write()` callback and replace with `pop_tick()` pull model
- Remove references to `PhoneAudioSink`
- Add a brief note about `VoiceRecvStats` metrics

**Step 2: Commit**

```
git add DESIGN.md
git commit -m "Update DESIGN.md for in-house discord_voice_rx module"
```

---

## Task 12: Final validation

**Step 1: Run all CI checks**

```
just          # lint, format, types, vulture
just test     # full test suite with coverage
```

**Step 2: Verify no references to old library remain**

```
grep -r "discord.ext.voice_recv\|discord-ext-voice-recv\|voice_recv\." src/ tests/ --include="*.py"
```

Should return zero results (except possibly comments in design docs).

**Step 3: Verify the package builds**

```
uv build
```

**Step 4: Run the Docker smoke test if available**

This confirms the production image builds with the new code.
