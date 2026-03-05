# Design: discord_voice_rx — In-house Discord Voice Receive

## Overview

A minimal, purpose-built module at `src/frizzle_phone/discord_voice_rx/` that replaces the external `discord-ext-voice-recv` dependency. It receives Discord voice audio, decrypts (transport + DAVE), decodes opus, buffers per-user, and exposes a `pop_tick()` API for the RTP send loop to pull synchronized per-user PCM frames every 20ms.

## Architecture

```
discord.py voice socket (SocketReader thread)
    |
    v
callback(packet_data: bytes)          <- registered via add_socket_listener()
    |
    +- parse RTP/RTCP header
    +- nacl transport decrypt (4 modes)
    +- DAVE decrypt (if session ready)
    +- push to decode queue -----------> queue.Queue
    |                                        |
    |                                        v
    |                              Decoder thread (1 thread)
    |                                +- per-SSRC jitter buffer (heap, 2-3 packets)
    |                                +- opus decode (with FEC for gaps)
    |                                +- stereo->mono conversion
    |                                +- append to per-user frame deque
    |                                        |
    |                                        v
    |                              per_user_buffers: dict[int, deque[ndarray]]
    |                                        |
    v                                        v
Gateway hook (SPEAKING op 5)         pop_tick() -> dict[int, ndarray]
    |                                   called by rtp_send_loop every 20ms
    +- SSRC -> user_id mapping
    +- CLIENT_DISCONNECT cleanup
```

## Key Design Decisions

1. **Consumer-driven pull (not library-driven tick):** The RTP send loop already has a carefully tuned 20ms clock with drift correction. The library just buffers and groups; the consumer calls `pop_tick()` on its own cadence. No second clock, no drift coordination.

2. **One decoder thread:** Opus decode is CPU-bound (~50-200us per packet). A single dedicated daemon thread handles the full pipeline from decrypted packet to per-user buffer. This avoids blocking the asyncio event loop while keeping the threading model simple.

3. **DAVE decryption is first-class:** No monkey-patches. DAVE decryption is built into the packet processing pipeline between transport-layer decryption and opus decode.

4. **All 4 nacl encryption modes supported:** Future-proof against Discord voice server variation. ~60 lines of code.

5. **Jitter buffer with FEC:** Per-SSRC heap-based reorder buffer (2-3 packets) before opus decode. Opus FEC recovers dropped packets. Cheap insurance against occasional UDP reordering.

6. **Built-in metrics:** `VoiceRecvStats` tracks packet, decode, buffer, and timing metrics with periodic ~5s log lines matching the `BridgeStats` pattern.

## Module Structure

```
src/frizzle_phone/discord_voice_rx/
+-- __init__.py          # public API: VoiceRecvClient, VoiceRecvStats
+-- client.py            # VoiceRecvClient (extends discord.VoiceClient)
+-- decrypt.py           # PacketDecryptor (4 nacl modes + DAVE)
+-- decoder.py           # Decoder thread: jitter buffer, opus decode, per-user buffering
+-- rtp.py               # RTP/RTCP packet parsing (header, extensions, silence detection)
+-- gateway.py           # Voice websocket hook (SPEAKING, CLIENT_DISCONNECT)
+-- stats.py             # VoiceRecvStats (periodic logging)
```

## Public API

```python
class VoiceRecvClient(discord.VoiceClient):
    """Voice client with receive capability."""

    def start_listening(self) -> None:
        """Start receiving voice audio. Registers socket listener, starts decoder thread."""

    def stop_listening(self) -> None:
        """Stop receiving. Cleans up decoder thread and buffers."""

    def pop_tick(self) -> dict[int, np.ndarray]:
        """Pop one frame per active user. Called by rtp_send_loop every 20ms.

        Returns dict mapping user_id -> mono int16 PCM array (960 samples, 48kHz).
        Empty dict if no audio available. Thread-safe.
        """

    def stop(self) -> None:
        """Stop both playing and listening."""

    @property
    def recv_stats(self) -> VoiceRecvStats: ...
```

## Changes to Existing Code

### bridge.py
- `PhoneAudioSink` deleted — no more sink class, drain(), or lock
- `stereo_to_mono()` moves into `decoder.py` (library responsibility)
- `rtp_send_loop` changes:
  - Replace `sink.drain()` + slot detection with `voice_client.pop_tick()`
  - Each tick returns a ready-to-mix `dict[int, ndarray]` — directly fed to `agc_bank.process_slot()` then `mix_slot()`
  - The slot queue is replaced by per-user frame deques inside the library

### bridge_manager.py
- Replace `voice_client.listen(sink)` with `voice_client.start_listening()`
- Remove sink from `BridgeHandle`
- Pass `voice_client` to `rtp_send_loop` instead of `sink`

### discord_patches.py
- Deleted entirely — DAVE decryption and opus error handling are built into the library

### sip/server.py
- `channel.connect(cls=voice_recv.VoiceRecvClient)` -> `channel.connect(cls=discord_voice_rx.VoiceRecvClient)`

### pyproject.toml
- Remove `discord-ext-voice-recv==0.5.2a179` dependency

## VoiceRecvStats

```python
class VoiceRecvStats:
    # Packet-level
    packets_in: int = 0              # total RTP packets received
    packets_decrypted: int = 0       # successfully decrypted
    packets_decrypt_failed: int = 0  # nacl/DAVE failures (skipped)
    packets_rtcp: int = 0            # RTCP packets (counted, not processed)

    # Decode
    opus_decodes: int = 0            # successful opus decodes
    opus_fec_recoveries: int = 0     # frames recovered via FEC
    opus_errors: int = 0             # corrupt opus packets (skipped)
    max_decode_us: int = 0           # peak opus decode time in us

    # Jitter buffer
    jitter_reordered: int = 0        # packets delivered out of order
    jitter_duplicates: int = 0       # duplicate packets dropped
    jitter_overflow: int = 0         # packets dropped (buffer full)

    # Per-user buffering
    buffer_depth_max: int = 0        # peak frames across any user
    tick_users_max: int = 0          # peak concurrent users in a tick
    ticks_empty: int = 0             # pop_tick() calls that returned empty
    ticks_served: int = 0            # pop_tick() calls with data

    # Timing
    max_callback_us: int = 0         # peak time in socket callback (decrypt)
    max_thread_loop_us: int = 0      # peak time in decoder thread iteration
```

Logged every ~5s alongside BridgeStats, grep-friendly with `voice_recv stats` prefix.

## Jitter Buffer

Per-SSRC `HeapJitterBuffer`:
- Min-heap ordered by sequence number
- Prefill: 2 packets before first output
- Max size: 10 packets (drop oldest on overflow)
- Handles sequence number wraparound (uint16)
- Feeds opus FEC: if a packet is missing, decode next packet with `fec=True`

## Decoder Thread

Single daemon thread:
1. Block on `queue.Queue.get()` (decrypted packets from socket callback)
2. Route to per-SSRC jitter buffer
3. Pop ready packets from jitter buffer
4. Opus decode -> stereo PCM
5. `stereo_to_mono()` -> mono int16 array
6. Append to `per_user_buffers[user_id]` (per-user deque, max 50 frames)
7. Record timing in `VoiceRecvStats`

`pop_tick()` (called from asyncio thread):
- Acquire lock, pop one frame from each non-empty user deque, release lock
- Returns `dict[int, ndarray]` — maps user_id -> mono PCM (960 samples)

## Gateway Hook

Minimal hook injected via `create_connection_state(hook=...)`:

- **Opcode 5 (SPEAKING):** Extract `user_id` and `ssrc`, update `_ssrc_to_id` / `_id_to_ssrc`
- **Opcode 13 (CLIENT_DISCONNECT):** Remove SSRC mapping, destroy decoder state

## Encryption

`PacketDecryptor` supporting all 4 modes, selected based on `voice_client.mode`:

| Mode | Nonce source |
|------|-------------|
| `xsalsa20_poly1305` | RTP header padded to 24 bytes |
| `xsalsa20_poly1305_suffix` | Last 24 bytes of payload |
| `xsalsa20_poly1305_lite` | Last 4 bytes padded to 24 |
| `aead_xchacha20_poly1305_rtpsize` | Last 4 bytes, RTP header as AAD |

DAVE decryption applied after transport decrypt when `dave_session.ready` and SSRC is mapped.
