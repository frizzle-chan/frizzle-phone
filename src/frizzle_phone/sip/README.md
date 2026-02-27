# SIP Transaction Layer

INVITE server transaction state machine per RFC 3261 §17.2.1 + RFC 6026.

## State Machine

```mermaid
stateDiagram-v2
    [*] --> Proceeding : INVITE received

    Proceeding --> Accepted : send 2xx (200 OK)
    Proceeding --> Proceeding : retransmitted INVITE / re-send 1xx

    Accepted --> Accepted : Timer G fires / retransmit 2xx, double G (cap 4s)
    Accepted --> Accepted : retransmitted INVITE / re-send 2xx
    Accepted --> Confirmed : ACK received / cancel G+H, start I
    Accepted --> Terminated : Timer H fires (32s) / ACK never came, teardown

    Confirmed --> Confirmed : ACK retransmission / absorb
    Confirmed --> Terminated : Timer I fires (5s)

    Terminated --> [*]
```

## Timers

| Timer | Start | Default | Behavior |
|-------|-------|---------|----------|
| **G** | On 2xx sent | T1 = 500ms | Retransmit 2xx, double interval each firing (cap at T2 = 4s) |
| **H** | On 2xx sent | 64 × T1 = 32s | Max wait for ACK — terminate transaction, send BYE |
| **I** | On ACK received | T4 = 5s | Absorb ACK retransmissions, then clean up |

## Call Flow

```mermaid
sequenceDiagram
    participant Phone
    participant Server
    participant Txn as Transaction

    Phone->>Server: INVITE (branch=B1)
    Server->>Txn: create(branch=B1)
    Server->>Phone: 100 Trying
    Server->>Txn: send_2xx(200 OK)
    Txn->>Phone: 200 OK
    Note right of Txn: Start Timer G (500ms), Timer H (32s)

    loop Timer G fires (no ACK yet)
        Txn->>Phone: 200 OK (retransmit)
        Note right of Txn: Double G interval (cap 4s)
    end

    Phone->>Server: ACK
    Server->>Txn: receive_ack()
    Note right of Txn: Cancel G+H, start Timer I (5s)

    Note over Server: RTP audio streaming

    Note right of Txn: Timer I fires → Terminated
```

## Files

| File | Purpose |
|------|---------|
| `transaction.py` | `InviteServerTxn` — state machine + timers G/H/I |
| `server.py` | `SipServer` — UDP protocol, call state, transaction integration |
| `message.py` | SIP message parsing (with compact header normalization) and response building |
| `sdp.py` | SDP offer parsing and answer generation |
