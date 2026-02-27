# RFC Section Index

Quick-reference index mapping protocol topics to exact RFC files and line ranges.
All files live in `rfcs/` relative to the project root.

## File Summary

| File | RFC | Topic | Lines |
|------|-----|-------|-------|
| `rfc3261.txt` | 3261 | Core SIP | 15,067 |
| `rfc2617.txt` | 2617 | HTTP Digest Authentication | 1,907 |
| `rfc4566.txt` | 4566 | SDP (Session Description Protocol) | 2,747 |
| `rfc3264.txt` | 3264 | SDP Offer/Answer Model | 1,403 |
| `rfc3550.txt` | 3550 | RTP | 5,827 |
| `rfc3551.txt` | 3551 | RTP Audio/Video Profile | 2,467 |
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
