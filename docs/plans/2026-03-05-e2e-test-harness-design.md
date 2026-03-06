# E2E Test Harness Design

## Problem

Manual testing of multi-speaker scenarios (e.g. 5 Discord users speaking into
a phone call) is difficult to reproduce. The existing test suite has strong
unit coverage for codecs, parsing, and audio mixing, but integration tests
rely heavily on mocking (27 mocks in `test_bridge_manager.py` alone). This
gives low confidence that the full SIP → bridge → RTP pipeline actually works
end-to-end.

The primary concern is the **Discord→Phone** path: multiple Discord speakers
mixed down and delivered as RTP to a SIP phone.

## Approach

Build the harness at the `BridgeManager.start()` seam. A `FakeVoiceRecvClient`
replaces the real Discord voice connection with deterministic audio sources
while the rest of the stack — SIP signaling, bridge wiring, RTP send loop,
UDP delivery — runs for real.

Minimal production code changes (two new protocols, no behavioral changes).

## Production Code Changes

### 1. VoiceConnector Protocol

Extract the guild lookup + `channel.connect()` logic from `_handle_invite_async`
into a protocol:

```python
class VoiceConnector(Protocol):
    async def connect(self, guild_id: int, channel_id: int) -> BridgeableVoiceClient: ...
```

**Default implementation** (new class, used in production): wraps
`bot.get_guild()` → `guild.get_channel()` → `channel.connect(cls=VoiceRecvClient)`.

**SipServer changes**: accepts an optional `voice_connector` parameter in
`__init__()`. Falls back to the default implementation when not provided.
`_handle_invite_async` delegates to `self._voice_connector.connect()` instead
of doing guild/channel lookup inline.

### 2. BridgeableVoiceClient Protocol

`BridgeManager.start()` currently takes `VoiceRecvClient`. Loosen to a protocol:

```python
class BridgeableVoiceClient(Protocol):
    def play(self, source: discord.AudioSource) -> None: ...
    def start_listening(self) -> None: ...
    def stop(self) -> None: ...
    def stop_listening(self) -> None: ...
    def pop_tick(self) -> dict[int, np.ndarray]: ...
```

Both `VoiceRecvClient` and `FakeVoiceRecvClient` satisfy this protocol without
any code changes to `VoiceRecvClient`.

`BridgeManager.start()` type hint changes from `VoiceRecvClient` to
`BridgeableVoiceClient`. No runtime behavior change.

## Test Components

### FakeVoiceRecvClient

Standalone class (does NOT subclass `discord.VoiceClient`) that satisfies
`BridgeableVoiceClient`. Constructed with a list of audio sources — one per
simulated Discord speaker.

- `pop_tick()` returns the next 960-sample (20ms at 48kHz) mono frame per
  active speaker, keyed by synthetic user ID
- `play(source)` accepts the `PhoneAudioSource` but discards it (Phone→Discord
  direction is not under test)
- `start_listening()` / `stop_listening()` / `stop()` are no-ops

Two audio modes:
- **Sine tones**: each speaker sends a distinct frequency (C major chord).
  Verifiable via FFT peaks in the RTP output
- **WAV files**: loads test speech files, resamples to 48kHz, slices into
  20ms frames. Verified via golden-file RMSE/correlation regression

### FakeVoiceConnector

Satisfies `VoiceConnector`. Returns a pre-configured `FakeVoiceRecvClient`
for a known guild/channel pair.

### RTP Collector

Reuse the `_RtpCollector` pattern from `test_e2e_multi_speaker.py` — a
`DatagramProtocol` that accumulates received RTP packets for assertion.

## Test Flow

```
1. Setup
   ├─ In-memory SQLite DB with seeded discord extension
   ├─ FakeVoiceRecvClient with 5 sine-tone speakers
   ├─ FakeVoiceConnector returning the fake client
   ├─ SipServer on 127.0.0.1:0 with fake connector
   └─ RtpCollector on 127.0.0.1:0 (the "phone")

2. SIP Signaling (reuse test_e2e_sip.py helpers)
   ├─ INVITE with SDP (audio port = collector's port)
   ├─ Receive 100 Trying + 200 OK (SDP answer has server's RTP port)
   └─ ACK → triggers _start_discord_bridge → BridgeManager.start()
            → real rtp_send_loop pulling from fake pop_tick()

3. Audio Collection (~4s for 200 ticks)
   └─ RTP packets arrive at collector via real UDP

4. Verification
   ├─ Decode PCMU payloads
   ├─ Sine mode: FFT, assert peaks at 5 frequencies
   ├─ WAV mode: golden-file regression (RMSE ≤ 30, correlation ≥ 0.999)
   └─ Assert BridgeStats (frames mixed, silence, no drops)

5. Teardown
   ├─ BYE → 200 OK
   └─ Assert clean shutdown (no leaked tasks/transports)
```

## What This Replaces

Once the harness exists, these tests become candidates for simplification:

- **test_bridge_manager.py** (27 mocks): E2E test covers the happy path
  through real `BridgeManager.start()`. Remaining value: lifecycle edge cases
  (double-stop, shutdown during setup) which can be smaller focused tests
- **test_rtp_send_loop.py** (7 patches): E2E runs the real send loop. Patches
  for `random.randint` / `time.monotonic` unnecessary since we verify audio
  output, not internal state

Tests that stay as-is: pure unit tests (AGC, codec, parsing),
`test_e2e_sip.py` (SIP protocol correctness), `test_e2e_multi_speaker.py`
(fast mixer-only regression).

## Future: Real Discord Layer

Optional smoke test with a real Discord bot, skipped by default:

- Requires `DISCORD_TEST_TOKEN` and `DISCORD_TEST_GUILD_ID` env vars
- Marked `@pytest.mark.discord`, skipped unless env vars present
- Second bot joins voice channel, plays audio
- SIP phone endpoint verifies RTP arrives
- Runs nightly in CI, not on every commit
