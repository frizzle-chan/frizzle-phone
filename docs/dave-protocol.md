# DAVE Protocol Reference

Discord Audio/Video Encryption (DAVE) is Discord's end-to-end encryption protocol for voice and video. It uses MLS (Messaging Layer Security, RFC 9420) for group key agreement and AES-128-GCM for frame encryption.

This document captures research findings relevant to our voice receive implementation.

## Protocol Layers

DAVE operates as a **frame-level** encryption layer on top of Discord's existing **transport-level** encryption (AES256-GCM or XChaCha20Poly1305 between client and SFU). The two layers are independent:

- Transport encryption: client <-> SFU (always active)
- DAVE E2EE: client <-> client (opt-in, layered on top)

## Voice Gateway Opcodes (DAVE-related)

| Opcode | Name | Direction | Purpose |
|--------|------|-----------|---------|
| 22 | `DAVE_EXECUTE_TRANSITION` | S->C | All members ready (or timeout): switch to new keys |
| 23 | `DAVE_READY_FOR_TRANSITION` | C->S | Client signals it has processed the new epoch |
| 24 | `DAVE_PREPARE_EPOCH` | S->C | Announces protocol version change / new MLS group |
| 25 | `DAVE_MLS_EXTERNAL_SENDER` | S->C | MLS external sender credential |
| 26 | `DAVE_MLS_KEY_PACKAGE` | C->S | Client's MLS key package |
| 27 | `DAVE_MLS_PROPOSALS` | S->C | MLS add/remove proposals |
| 28 | `DAVE_MLS_COMMIT_WELCOME` | C->S | Client's commit + welcome messages |
| 29 | `DAVE_MLS_ANNOUNCE_COMMIT` | S->C | Winning commit broadcast |
| 30 | `DAVE_MLS_WELCOME` | S->C | Welcome message for new members |
| 31 | `DAVE_MLS_INVALID_COMMIT` | C->S | Client reports invalid commit/welcome |

## Epoch Lifecycle

1. **Prepare**: Gateway sends opcode 24 (`DAVE_PREPARE_EPOCH`).
   - `epoch = 1`: New MLS group creation (fresh start or sole-member reset).
   - `epoch > 1`: Existing group advancing (protocol version change).
2. **Key exchange**: Clients send key packages (op 26), gateway distributes proposals (op 27), committer sends commit+welcome (op 28).
3. **Announce**: Gateway broadcasts winning commit (op 29) with a `transition_id`.
4. **Ready**: Clients process the commit, derive new sender key ratchets, signal readiness (op 23).
5. **Execute**: Once all members are ready **or ~2 second timeout expires**, gateway sends opcode 22.
6. **Switch**: Senders begin encrypting with new epoch keys. Receivers retain old epoch keys for up to **10 seconds**.

Epoch advances are triggered by: member join, member leave, protocol version change, or sole-member reset.

## Key Ratchets

Each sender has a per-epoch key ratchet derived from the MLS group secret:

- **Derivation**: MLS export with label `"Discord Secure Frames v0"` + sender's 64-bit user ID, producing a 16-byte base secret.
- **Hash ratchet** (RFC 9420 Section 9.1): Forward-only derivation chain producing per-generation keys (AES-128-GCM key + 12-byte nonce).
- **Generations**: Determined by bits 24-31 of the 32-bit truncated sync nonce. A new generation starts every 2^24 (~16.7M) frames.
- **Generation wrap**: 256 (one byte). At max frame rate (170 fps), a generation lasts ~27.4 hours.

### Key Constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `CIPHER_EXPIRY` | 10 seconds | Old generation cryptor lifetime after newer generation seen |
| `RATCHET_EXPIRY` | 10 seconds | Old epoch ratchet lifetime after transition |
| `MAX_GENERATION_GAP` | 250 | Max acceptable generation ahead of current |
| `MAX_MISSING_NONCES` | 1000 | Replay protection window |
| `MAX_FRAMES_PER_SECOND` | 170 | 50 audio + 120 video |

## Passthrough Mode

Passthrough allows unencrypted frames through the decryptor. The decryptor distinguishes encrypted from unencrypted frames by checking for a magic marker (`0xFAFA`) at the end of the frame.

### davey/libdave API

```
set_passthrough_mode(passthrough_mode: bool, transition_expiry: int | None = None)
```

- `set_passthrough_mode(True, ...)`: Sets `allow_passthrough_until = None` (**permanent** passthrough). The expiry argument is **ignored**.
- `set_passthrough_mode(False, N)`: Sets `allow_passthrough_until = now + N seconds` (timed grace period, then passthrough disabled).

When passthrough is disabled and an unencrypted frame arrives, the decryptor returns `DecryptionFailed(UnencryptedWhenPassthroughDisabled)`.

### Special Cases

- **Opus silence** (`0xF8FFFE`): Always passed through regardless of passthrough state. The DAVE spec acknowledges this as a known attack surface (SFU silence synthesis).
- **Downgrade to protocol v0**: Passthrough enabled immediately, old keys retained for transition period.

## Decryptor Architecture

Both libdave (C++) and davey (Rust) maintain a **deque of cryptor managers**, allowing old and new epoch keys to coexist:

1. `transition_to_key_ratchet()`: Pushes new manager to back, sets expiry on old managers (10s).
2. `cleanup_expired_cryptor_managers()`: Pops expired managers from front.
3. Decrypt: Tries all managers (libdave: newest-first; davey: front-to-back) until one succeeds.

This design handles the ~10 second window where in-flight packets from the old epoch may still arrive after a transition.

## Session Lifecycle

- **No TTL**: DAVE sessions persist for the voice connection duration.
- **Recovery from invalid commit**: Client sends opcode 31, gateway removes and re-adds the client. discord.py calls `reinit_dave_session()`.
- **Missed transitions**: If a client can't process a commit (missed intermediate proposals), it enters the invalid-commit recovery path.
- **No retransmission**: The protocol has no mechanism to request missed key material. Recovery is always via session reset.

## discord.py Internal Handling

discord.py (PR #10300, using `davey`) handles DAVE transitions internally:

| Trigger | Action |
|---------|--------|
| Opcode 24, epoch=1 | `reinit_dave_session()` (creates new MLS group + enables passthrough) |
| Opcode 24, epoch>1 | **No-op** (known gap) |
| Downgrade to protocol v0 | `set_passthrough_mode(True, 120)` |
| Session reset | `set_passthrough_mode(True, 10)` |
| Upgrade from downgrade | `set_passthrough_mode(True, 10)` |
| Execute transition (op 22) | Processes commit, derives new key ratchets |

discord.py does **not** expose DAVE session management to library consumers. It manages all transitions internally via `VoiceConnectionState` and `DiscordVoiceWebSocket`.

## Ecosystem DAVE Support

| Library | Language | DAVE | Voice Receive |
|---------|----------|------|---------------|
| libdave | C++ | Full (reference impl) | Yes |
| davey | Rust/Py/Node | Full | Yes |
| discord.py (PR #10300) | Python | In PR, not merged | Via davey |
| discord.js (@discordjs/voice) | JS/TS | Merged | Broken in v0.19.x |
| Songbird | Rust | None | None |
| discord-ext-voice-recv | Python | None | None |
| DSharpPlus | C# | None | None |

## References

- [DAVE Protocol Whitepaper](https://daveprotocol.com/)
- [discord/dave-protocol](https://github.com/discord/dave-protocol/blob/main/protocol.md) (protocol spec)
- [discord/libdave](https://github.com/discord/libdave) (C++ reference implementation)
- [Snazzah/davey](https://github.com/Snazzah/davey) (Rust implementation, Python/Node bindings)
- [discord.py PR #10300](https://github.com/Rapptz/discord.py/pull/10300) (DAVE support)
- [discord.js PR #10921](https://github.com/discordjs/discord.js/pull/10921) (DAVE support)
- [discord.js issue #11419](https://github.com/discordjs/discord.js/issues/11419) (receive-side DAVE bug)
- [Discord Blog: Meet DAVE](https://discord.com/blog/meet-dave-e2ee-for-audio-video)
- [RFC 9420](https://www.rfc-editor.org/rfc/rfc9420) (Messaging Layer Security)
