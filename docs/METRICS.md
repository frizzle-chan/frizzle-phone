# Prometheus Metrics Reference

frizzle-phone exposes Prometheus metrics on `GET /metrics` (port 8080). Scrape interval: 15–30s recommended (metrics update every 5s internally).

## Metric Inventory

### Bridge Counters (`frizzle_bridge_*`)

Updated every 5s from `BridgeStats.log_and_reset()`. Values are monotonically increasing totals.

| Metric | Description |
|---|---|
| `frizzle_bridge_d2p_frames_mixed_total` | Discord→phone frames mixed into RTP slots |
| `frizzle_bridge_d2p_frames_dropped_total` | Discord→phone frames dropped (slot queue freshness eviction) |
| `frizzle_bridge_p2d_frames_in_total` | Phone→Discord RTP frames received |
| `frizzle_bridge_p2d_queue_overflow_total` | Phone→Discord queue overflows (queue full, oldest dropped) |
| `frizzle_bridge_p2d_reads_total` | Phone→Discord `read()` calls from Discord voice sink |
| `frizzle_bridge_p2d_silence_reads_total` | Phone→Discord reads that returned silence (queue empty) |
| `frizzle_bridge_p2d_gap_warnings_total` | Phone→Discord recv gaps >40ms (jitter/network issues) |
| `frizzle_bridge_rtp_frames_sent_total` | RTP frames sent to phone (audio + silence) |
| `frizzle_bridge_rtp_silence_sent_total` | RTP silence frames sent to phone |

### Bridge Gauges

Set to the value observed in the most recent 5s snapshot window.

| Metric | Description |
|---|---|
| `frizzle_bridge_d2p_queue_depth` | Discord→phone slot queue depth at snapshot time |
| `frizzle_bridge_p2d_max_recv_gap_seconds` | Largest gap between consecutive phone RTP packets (seconds) |
| `frizzle_bridge_rtp_max_sleep_overshoot_seconds` | Largest RTP send loop timing overshoot (seconds) |

### Voice Receive Counters (`frizzle_voice_rx_*`)

Updated every 5s from `VoiceRecvStats.log_and_reset()`.

| Metric | Description |
|---|---|
| `frizzle_voice_rx_packets_in_total` | Discord voice UDP packets received |
| `frizzle_voice_rx_decrypt_failures_total` | Packets that failed decryption (NaCl/DAVE) |
| `frizzle_voice_rx_opus_decodes_total` | Successful Opus frame decodes |
| `frizzle_voice_rx_opus_errors_total` | Opus decode errors |
| `frizzle_voice_rx_ticks_empty_total` | `pop_tick()` calls that returned no frames |
| `frizzle_voice_rx_ticks_served_total` | `pop_tick()` calls that returned frames |

### Voice Receive Gauges

| Metric | Description |
|---|---|
| `frizzle_voice_rx_max_callback_microseconds` | Peak socket callback duration (μs) in last 5s window |
| `frizzle_voice_rx_max_decode_microseconds` | Peak Opus decode duration (μs) in last 5s window |

### SIP Server

| Metric | Description |
|---|---|
| `frizzle_active_calls` | Current number of active SIP calls (refreshed at scrape time) |

## Interpreting Metrics

### Healthy Call

During a normal bidirectional call with one Discord speaker:

- `rate(frizzle_bridge_rtp_frames_sent_total[1m])` ≈ 50/s (one 20ms frame per tick)
- `rate(frizzle_bridge_d2p_frames_mixed_total[1m])` > 0 (Discord audio flowing)
- `frizzle_bridge_d2p_frames_dropped_total` stable (no drops)
- `frizzle_bridge_rtp_max_sleep_overshoot_seconds` < 0.005 (5ms)
- `rate(frizzle_voice_rx_packets_in_total[1m])` ≈ 50/s per speaker
- `frizzle_voice_rx_decrypt_failures_total` stable (no failures)
- `frizzle_active_calls` ≥ 1

### Silence vs Pipeline Loss

When nobody is speaking on Discord, silence is expected and healthy:
- `rtp_silence_sent` high + `d2p_frames_mixed` = 0 → **normal silence**, no speakers active
- `rtp_silence_sent` high + `d2p_frames_mixed` also high → **pipeline loss**, frames consumed but not making it to RTP

PromQL to detect pipeline loss:
```promql
(
  rate(frizzle_bridge_rtp_silence_sent_total[5m])
  - clamp_min(rate(frizzle_bridge_rtp_frames_sent_total[5m]) - rate(frizzle_bridge_d2p_frames_mixed_total[5m]), 0)
) / rate(frizzle_bridge_rtp_frames_sent_total[5m]) > 0.10
```

### Phone Audio Not Arriving

- `rate(frizzle_bridge_p2d_frames_in_total[1m])` ≈ 0 → phone not sending RTP
- `rate(frizzle_bridge_p2d_silence_reads_total[1m]) / rate(frizzle_bridge_p2d_reads_total[1m])` > 0.20 → phone audio underflow

### Jitter / Network Issues

- `frizzle_bridge_p2d_max_recv_gap_seconds` > 0.040 → phone-side jitter or packet loss
- `rate(frizzle_bridge_p2d_gap_warnings_total[5m])` > 0 → sustained recv gaps
- `frizzle_bridge_rtp_max_sleep_overshoot_seconds` > 0.005 → event loop congestion

### Discord Voice Issues

- `rate(frizzle_voice_rx_decrypt_failures_total[5m])` > 0 → encryption key rotation issue or corrupt packets
- `rate(frizzle_voice_rx_opus_errors_total[5m])` > 0 → malformed Opus frames from Discord
- `frizzle_voice_rx_max_callback_microseconds` > 1000 → socket callback taking too long (>1ms)

### Queue Health

- `frizzle_bridge_d2p_queue_depth` > 25 → slot queue building up (approaching 50-slot cap)
- `rate(frizzle_bridge_p2d_queue_overflow_total[5m])` > 0 → phone→Discord queue full, dropping audio
- `rate(frizzle_bridge_d2p_frames_dropped_total[5m])` > 0 → freshness eviction in d2p slot queue

## Example Grafana Queries

**Call volume:**
```promql
frizzle_active_calls
```

**RTP send rate (should be ~50/s during calls):**
```promql
rate(frizzle_bridge_rtp_frames_sent_total[1m])
```

**Silence ratio (lower is better during active speech):**
```promql
rate(frizzle_bridge_rtp_silence_sent_total[1m]) / rate(frizzle_bridge_rtp_frames_sent_total[1m])
```

**Decrypt failure rate:**
```promql
rate(frizzle_voice_rx_decrypt_failures_total[5m])
```

**Peak timing overshoot:**
```promql
frizzle_bridge_rtp_max_sleep_overshoot_seconds
```

## Alert Examples

**No active calls when expected:**
```yaml
- alert: FrizzleNoActiveCalls
  expr: frizzle_active_calls == 0
  for: 10m
```

**High pipeline loss:**
```yaml
- alert: FrizzlePipelineLoss
  expr: >
    rate(frizzle_bridge_d2p_frames_dropped_total[5m]) > 1
  for: 2m
```

**Decrypt failures:**
```yaml
- alert: FrizzleDecryptFailures
  expr: rate(frizzle_voice_rx_decrypt_failures_total[5m]) > 0
  for: 1m
```
