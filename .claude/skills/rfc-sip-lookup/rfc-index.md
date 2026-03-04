# RFC Section Index

Quick-reference index mapping protocol topics to exact RFC files and line ranges.
All files live in `rfcs/` relative to the project root.

## File Summary

| File | RFC | Topic | Lines |
|------|-----|-------|-------|
| `rfc3261.txt` | 3261 | Core SIP | 15,067 |
| `rfc6026.txt` | 6026 | INVITE Transaction Revision (Accepted state) | 1,123 |
| `rfc3581.txt` | 3581 | rport / Symmetric Response Routing | 731 |
| `rfc5626.txt` | 5626 | SIP Outbound / Keepalive | 2,803 |
| `rfc2617.txt` | 2617 | HTTP Digest Authentication | 1,907 |
| `rfc4566.txt` | 4566 | SDP (Session Description Protocol) | 2,747 |
| `rfc3264.txt` | 3264 | SDP Offer/Answer Model | 1,403 |
| `rfc3550.txt` | 3550 | RTP | 5,827 |
| `rfc3551.txt` | 3551 | RTP Audio/Video Profile | 2,467 |
| `rfc4733.txt` | 4733 | DTMF / Telephone-Event RTP Payload | 2,747 |
| `rfc3389.txt` | 3389 | Comfort Noise RTP Payload | 451 |
| `rfc3665.txt` | 3665 | SIP Basic Call Flow Examples | 5,267 |

---

## Registration & Auth

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3261 §10.3 | `rfc3261.txt` | 3496–3836 | Processing REGISTER requests (registrar behavior) |
| RFC 3261 §22 | `rfc3261.txt` | 10773–11215 | HTTP digest auth framework for SIP |
| RFC 2617 §3 | `rfc2617.txt` | 310–380 | Digest Access Authentication overview |
| RFC 2617 §3.2.1 | `rfc2617.txt` | 399–574 | WWW-Authenticate response header (server challenge) |
| RFC 2617 §3.2.2 | `rfc2617.txt` | 575–831 | Authorization request header (client response) |
| RFC 3665 §2 | `rfc3665.txt` | 210–622 | Registration call flow examples (success, update, cancel, failure) |

## Call Setup (INVITE)

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3261 §13 | `rfc3261.txt` | 4301–4961 | Initiating a Session — full INVITE processing |
| RFC 3261 §12 | `rfc3261.txt` | 3837–4300 | Dialog creation and management |
| RFC 3665 §3 | `rfc3665.txt` | 623–5046 | Session establishment call flow examples |
| RFC 3665 §3.1 | `rfc3665.txt` | 638–790 | Successful session establishment (annotated INVITE flow) |

## Call Teardown

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3261 §15.1 | `rfc3261.txt` | 5023–5077 | Terminating a Session — BYE processing |
| RFC 3261 §9 | `rfc3261.txt` | 2929–3495 | Canceling a Request — CANCEL processing |

## SIP Message Format

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3261 §7 | `rfc3261.txt` | 1451–2529 | Message structure, headers, bodies |
| RFC 3261 §8.2 | `rfc3261.txt` | 2530–2928 | UAS behavior (how to process incoming requests) |

## Transactions & Timers

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3261 §17.2 | `rfc3261.txt` | 7460–7867 | Server transaction overview |
| RFC 3261 §17.2.1 | `rfc3261.txt` | 7471–7633 | INVITE server transaction state machine (Timer G/H/I) |
| RFC 3261 §17.2.2 | `rfc3261.txt` | 7634–7678 | Non-INVITE server transaction state machine (Timer J) |
| RFC 6026 §7 | `rfc6026.txt` | 252–566 | Change details — Accepted state for 2xx retransmission |
| RFC 6026 §8 | `rfc6026.txt` | 567–994 | Exact changes to RFC 3261 (revised state machine figures) |

## NAT Traversal & Response Routing

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3581 §3 | `rfc3581.txt` | 135–183 | Client behavior — sending rport in Via |
| RFC 3581 §4 | `rfc3581.txt` | 184–230 | Server behavior — populating rport, response routing |
| RFC 3581 §6 | `rfc3581.txt` | 244–296 | Example message exchange with rport |

## Keepalive

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 5626 §3 | `rfc5626.txt` | 296–689 | Overview of outbound connection management |
| RFC 5626 §4.4.1 | `rfc5626.txt` | 690–1221 | UA procedures (includes CRLF keepalive) |
| RFC 5626 §8 | `rfc5626.txt` | 1543–1638 | STUN keep-alive processing |

## Transport

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3261 §18 | `rfc3261.txt` | 7868–8219 | UDP/TCP transport layer |

## SDP (Session Description Protocol)

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 4566 §5 | `rfc4566.txt` | 382–1328 | Full SDP specification (all field definitions) |
| RFC 4566 §5.1 | `rfc4566.txt` | 551–566 | Protocol version (`v=`) |
| RFC 4566 §5.2 | `rfc4566.txt` | 567–633 | Origin (`o=`) |
| RFC 4566 §5.7 | `rfc4566.txt` | 739–856 | Connection data (`c=`) |
| RFC 4566 §5.9 | `rfc4566.txt` | 914–967 | Timing (`t=`) |
| RFC 4566 §5.14 | `rfc4566.txt` | 1197–1328 | Media descriptions (`m=`) |
| RFC 4566 §5.13 | `rfc4566.txt` | 1140–1196 | Attributes (`a=`) |
| RFC 4566 §6 | `rfc4566.txt` | 1329–1692 | SDP attributes reference |
| RFC 4566 §9 | `rfc4566.txt` | 2141–2425 | SDP grammar (ABNF) |

## SDP Offer/Answer

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3264 §5 | `rfc3264.txt` | 236–457 | Generating the initial offer |
| RFC 3264 §6 | `rfc3264.txt` | 458–648 | Generating the answer |
| RFC 3264 §7 | `rfc3264.txt` | 649–678 | Offerer processing of the answer |
| RFC 3264 §8 | `rfc3264.txt` | 679–976 | Modifying the session |

## RTP (Real-time Transport Protocol)

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3550 §5 | `rfc3550.txt` | 679–958 | RTP data transfer protocol (header format) |
| RFC 3550 §5.1 | `rfc3550.txt` | 681–882 | RTP fixed header fields |
| RFC 3550 §5.3 | `rfc3550.txt` | 959–997 | Profile-specific modifications to the RTP header |

## RTP Audio/Video Profile

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3551 §4 | `rfc3551.txt` | 399–1630 | Audio encodings |
| RFC 3551 §4.5 | `rfc3551.txt` | 623–1630 | Audio encoding definitions |
| RFC 3551 §4.5.14 | `rfc3551.txt` | 1538–1552 | PCMA and PCMU codec definitions |
| RFC 3551 §6 | `rfc3551.txt` | 1756–1888 | Payload type definitions (static assignment table) |

## DTMF / Telephone Events

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 4733 §2 | `rfc4733.txt` | 399–1275 | RTP payload format for named telephone events |
| RFC 4733 §2.3 | `rfc4733.txt` | 449–600 | Payload format — header, duration, volume fields |
| RFC 4733 §2.5 | `rfc4733.txt` | 700–900 | Redundancy and reliability of event packets |
| RFC 4733 §3 | `rfc4733.txt` | 1276–1406 | Specification of event codes for DTMF (0-9, *, #, A-D) |
| RFC 4733 §5 | `rfc4733.txt` | 1687–2078 | Examples (single DTMF digit, long press, multiple digits) |

## Comfort Noise

| Section | File | Lines | What's here |
|---------|------|-------|-------------|
| RFC 3389 §2 | `rfc3389.txt` | 40–77 | Introduction — CN purpose and overview |
| RFC 3389 §3 | `rfc3389.txt` | 78–155 | CN payload definition (noise level, spectral info, packing) |
| RFC 3389 §4 | `rfc3389.txt` | 156–179 | Usage of RTP — payload type, timestamp, marker bit |
| RFC 3389 §5 | `rfc3389.txt` | 180–252 | Guidelines for use (VAD, transitions, SDP negotiation) |
