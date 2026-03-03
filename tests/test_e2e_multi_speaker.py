"""E2E multi-speaker chord stress test: 5 concurrent sine tones → mix → RTP → UDP."""

import asyncio
import time
from functools import partial

import numpy as np
import pytest

from frizzle_phone.bridge import PhoneAudioSink, rtp_send_loop
from frizzle_phone.bridge_stats import BridgeStats
from frizzle_phone.rtp.pcmu import ulaw_to_pcm
from tests.audio_helpers import pcm_to_wav, wav_samples_check

# C major chord frequencies (Hz)
FREQS = [261.63, 329.63, 392.00, 523.25, 659.25]
NUM_SPEAKERS = 5
AMPLITUDE = 6000  # 5 * 6000 = 30000 < 32767, no clipping
SAMPLE_RATE = 48000
FRAME_SAMPLES = 960  # 20ms at 48kHz

SILENCE_TICKS = 5
STAGGER_TICKS = 40
TOTAL_TICKS = SILENCE_TICKS + STAGGER_TICKS * NUM_SPEAKERS  # 205


def _generate_tone_frames(freq_hz: float, n_frames: int) -> list[np.ndarray]:
    """Generate phase-continuous sine wave, sliced into 960-sample mono frames."""
    total_samples = n_frames * FRAME_SAMPLES
    t = np.arange(total_samples, dtype=np.float64) / SAMPLE_RATE
    wave = (AMPLITUDE * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.int16)
    return [wave[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES] for i in range(n_frames)]


def _build_tick_data() -> list[list[tuple[int, np.ndarray]]]:
    """Build per-tick frame lists for staggered speaker entry.

    Timeline:
        Ticks 0-4:     silence (empty lists)
        Ticks 5-44:    speaker 1 only (C4)
        Ticks 45-84:   speakers 1-2 (C4+E4)
        Ticks 85-124:  speakers 1-3 (C4+E4+G4)
        Ticks 125-164: speakers 1-4 (C4+E4+G4+C5)
        Ticks 165-204: speakers 1-5 (full chord)
    """
    # Each speaker plays from its entry tick until the end
    speaker_entry = [SILENCE_TICKS + i * STAGGER_TICKS for i in range(NUM_SPEAKERS)]
    speaker_n_frames = [TOTAL_TICKS - entry for entry in speaker_entry]

    # Pre-generate all frames per speaker
    all_frames: list[list[np.ndarray]] = []
    for i in range(NUM_SPEAKERS):
        all_frames.append(_generate_tone_frames(FREQS[i], speaker_n_frames[i]))

    # Build tick data
    ticks: list[list[tuple[int, np.ndarray]]] = []
    for tick in range(TOTAL_TICKS):
        frame_list: list[tuple[int, np.ndarray]] = []
        for spk in range(NUM_SPEAKERS):
            entry = speaker_entry[spk]
            if tick >= entry:
                frame_idx = tick - entry
                user_id = spk + 1  # user IDs 1..5
                frame_list.append((user_id, all_frames[spk][frame_idx]))
        ticks.append(frame_list)
    return ticks


def _parse_rtp_payload(data: bytes) -> bytes:
    """Extract payload from an RTP packet (skip fixed 12-byte header)."""
    return data[12:] if len(data) > 12 else b""


class _RtpCollector(asyncio.DatagramProtocol):
    """Receives UDP datagrams into a list."""

    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.packets.append(data)


class _BurstyMultiPacedSink(PhoneAudioSink):
    """Sink that delivers frames in bursts, simulating Discord's real behavior.

    Discord often delivers multiple ticks' worth of frames in a single burst
    with gaps of up to 200ms between bursts. This sink pre-computes drain
    results so that burst deliveries alternate with empty drains, keeping
    the total drain count equal to the non-bursty case.
    """

    def __init__(
        self,
        tick_data: list[list[tuple[int, np.ndarray]]],
        burst_sizes: list[int],
    ) -> None:
        super().__init__()
        self._drains: list[list[tuple[int, np.ndarray]]] = []
        tick_idx = 0
        burst_idx = 0
        while tick_idx < len(tick_data):
            n = burst_sizes[burst_idx % len(burst_sizes)]
            combined: list[tuple[int, np.ndarray]] = []
            for _ in range(n):
                if tick_idx < len(tick_data):
                    combined.extend(tick_data[tick_idx])
                    tick_idx += 1
            self._drains.append(combined)
            for _ in range(n - 1):
                self._drains.append([])
            burst_idx += 1
        self._idx = 0

    def drain(self) -> list[tuple[int, np.ndarray]]:
        if self._idx < len(self._drains):
            frames = self._drains[self._idx]
            self._idx += 1
            return frames
        return []


class _MultiPacedSink(PhoneAudioSink):
    """Sink that returns pre-computed per-tick frame lists from drain().

    Generalises _PacedSink from test_e2e_audio.py to variable-length lists
    (0..N frames per tick) for multi-speaker scenarios.
    """

    def __init__(self, tick_data: list[list[tuple[int, np.ndarray]]]) -> None:
        super().__init__()
        self._tick_data = tick_data
        self._tick_idx = 0

    def drain(self) -> list[tuple[int, np.ndarray]]:
        if self._tick_idx < len(self._tick_data):
            frames = self._tick_data[self._tick_idx]
            self._tick_idx += 1
            return frames
        return []


@pytest.mark.asyncio
async def test_multi_speaker_chord_stagger(file_regression):
    """5 speakers stagger in → full chord, golden WAV + stats."""
    tick_data = _build_tick_data()
    assert len(tick_data) == TOTAL_TICKS

    sink = _MultiPacedSink(tick_data)
    stats = BridgeStats()
    # Prevent immediate reset: ensure _last_summary is recent so the 5s
    # interval doesn't elapse during our ~4.1s test run.
    stats._last_summary = time.monotonic()

    loop = asyncio.get_running_loop()
    collector = _RtpCollector()
    recv_transport, _ = await loop.create_datagram_endpoint(
        lambda: collector, local_addr=("127.0.0.1", 0)
    )
    recv_port = recv_transport.get_extra_info("sockname")[1]
    send_transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("127.0.0.1", recv_port)
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        rtp_send_loop(
            sink,
            send_transport,
            ("127.0.0.1", recv_port),
            stop_event=stop_event,
            stats=stats,
        )
    )

    # Wait for all packets (real 20ms pacing ≈ 4.1s for 205 ticks)
    for _ in range(2500):
        await asyncio.sleep(0.01)
        if len(collector.packets) >= TOTAL_TICKS:
            break

    stop_event.set()
    await task

    send_transport.close()
    recv_transport.close()

    assert len(collector.packets) >= TOTAL_TICKS, (
        f"Only received {len(collector.packets)}/{TOTAL_TICKS} packets"
    )

    # --- Stats assertions ---
    assert stats.d2p_frames_mixed == 200, (
        f"Expected 200 mixed frames, got {stats.d2p_frames_mixed}"
    )
    assert stats.d2p_frames_dropped == 0, (
        f"Expected 0 dropped, got {stats.d2p_frames_dropped}"
    )
    assert stats.d2p_stale_flush == 0, (
        f"Expected 0 stale flushes, got {stats.d2p_stale_flush}"
    )
    assert stats.rtp_frames_sent == TOTAL_TICKS, (
        f"Expected {TOTAL_TICKS} RTP sent, got {stats.rtp_frames_sent}"
    )
    assert stats.rtp_silence_sent == SILENCE_TICKS, (
        f"Expected {SILENCE_TICKS} silence, got {stats.rtp_silence_sent}"
    )

    # --- Golden file ---
    received_ulaw = b""
    for pkt in collector.packets[:TOTAL_TICKS]:
        received_ulaw += _parse_rtp_payload(pkt)

    pcm_8k = ulaw_to_pcm(received_ulaw)
    wav_bytes = pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)

    check_fn = partial(wav_samples_check, max_rmse=30.0, min_correlation=0.999)
    file_regression.check(wav_bytes, binary=True, extension=".wav", check_fn=check_fn)


@pytest.mark.asyncio
async def test_multi_speaker_burst_delivery(file_regression):
    """Bursty Discord delivery (up to 3 ticks at once) → same golden WAV."""
    tick_data = _build_tick_data()
    assert len(tick_data) == TOTAL_TICKS

    burst_sizes = [2, 1, 3, 1]
    sink = _BurstyMultiPacedSink(tick_data, burst_sizes)
    stats = BridgeStats()
    stats._last_summary = time.monotonic()

    loop = asyncio.get_running_loop()
    collector = _RtpCollector()
    recv_transport, _ = await loop.create_datagram_endpoint(
        lambda: collector, local_addr=("127.0.0.1", 0)
    )
    recv_port = recv_transport.get_extra_info("sockname")[1]
    send_transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("127.0.0.1", recv_port)
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        rtp_send_loop(
            sink,
            send_transport,
            ("127.0.0.1", recv_port),
            stop_event=stop_event,
            stats=stats,
        )
    )

    for _ in range(2500):
        await asyncio.sleep(0.01)
        if len(collector.packets) >= TOTAL_TICKS:
            break

    stop_event.set()
    await task

    send_transport.close()
    recv_transport.close()

    assert len(collector.packets) >= TOTAL_TICKS, (
        f"Only received {len(collector.packets)}/{TOTAL_TICKS} packets"
    )

    # --- Stats assertions ---
    assert stats.d2p_frames_mixed == 200, (
        f"Expected 200 mixed frames, got {stats.d2p_frames_mixed}"
    )
    assert stats.d2p_frames_dropped == 0, (
        f"Expected 0 dropped, got {stats.d2p_frames_dropped}"
    )
    assert stats.d2p_stale_flush == 0, (
        f"Expected 0 stale flushes, got {stats.d2p_stale_flush}"
    )
    assert stats.rtp_frames_sent == TOTAL_TICKS, (
        f"Expected {TOTAL_TICKS} RTP sent, got {stats.rtp_frames_sent}"
    )
    assert stats.rtp_silence_sent == SILENCE_TICKS, (
        f"Expected {SILENCE_TICKS} silence, got {stats.rtp_silence_sent}"
    )
    assert stats.d2p_queue_depth >= 2, (
        f"Expected queue depth >= 2 (burst buffering), got {stats.d2p_queue_depth}"
    )

    # --- Golden file ---
    received_ulaw = b""
    for pkt in collector.packets[:TOTAL_TICKS]:
        received_ulaw += _parse_rtp_payload(pkt)

    pcm_8k = ulaw_to_pcm(received_ulaw)
    wav_bytes = pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)

    check_fn = partial(wav_samples_check, max_rmse=30.0, min_correlation=0.999)
    file_regression.check(wav_bytes, binary=True, extension=".wav", check_fn=check_fn)


async def _run_burst_test(
    burst_sizes: list[int],
) -> BridgeStats:
    """Run the send loop with bursty delivery and return stats."""
    tick_data = _build_tick_data()
    sink = _BurstyMultiPacedSink(tick_data, burst_sizes)
    stats = BridgeStats()
    stats._last_summary = time.monotonic()

    loop = asyncio.get_running_loop()
    collector = _RtpCollector()
    recv_transport, _ = await loop.create_datagram_endpoint(
        lambda: collector, local_addr=("127.0.0.1", 0)
    )
    recv_port = recv_transport.get_extra_info("sockname")[1]
    send_transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("127.0.0.1", recv_port)
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        rtp_send_loop(
            sink,
            send_transport,
            ("127.0.0.1", recv_port),
            stop_event=stop_event,
            stats=stats,
        )
    )

    for _ in range(2500):
        await asyncio.sleep(0.01)
        if len(collector.packets) >= TOTAL_TICKS:
            break

    stop_event.set()
    await task

    send_transport.close()
    recv_transport.close()

    assert len(collector.packets) >= TOTAL_TICKS, (
        f"Only received {len(collector.packets)}/{TOTAL_TICKS} packets"
    )
    return stats


@pytest.mark.asyncio
async def test_burst_delivery_stress_no_drops():
    """Bursts up to 50 frames (1s) are fully absorbed with zero drops.

    With the old MAX_SLOT_QUEUE=5 these bursts would overflow massively.
    """
    stats = await _run_burst_test(burst_sizes=[50, 1, 25, 1, 40, 1])

    assert stats.d2p_frames_dropped == 0, (
        f"Expected 0 dropped frames, got {stats.d2p_frames_dropped} "
        f"(queue depth hit {stats.d2p_queue_depth})"
    )
    assert stats.d2p_queue_depth >= 20, (
        f"Expected queue depth >= 20 (large burst), got {stats.d2p_queue_depth}"
    )


@pytest.mark.asyncio
async def test_burst_delivery_stress_bounded_drops():
    """Bursts of 100 frames (2s) overflow the queue but drops are bounded.

    The pattern cycles twice over 205 ticks, producing two 100-frame bursts.
    Each burst overflows the 50-slot queue, dropping ~50 oldest frames per
    burst (~95 total).  With MAX_SLOT_QUEUE=5, the same pattern would drop
    ~190 frames — this verifies the larger queue cuts drops roughly in half.
    """
    stats = await _run_burst_test(burst_sizes=[100, 1, 1, 1])

    assert stats.d2p_frames_dropped > 0, "Expected some drops from 100-frame bursts"
    assert stats.d2p_frames_dropped <= 100, (
        f"Drops should be bounded (~95), got {stats.d2p_frames_dropped}"
    )
    assert stats.d2p_queue_depth == 50, (
        f"Queue should have hit cap of 50, got {stats.d2p_queue_depth}"
    )
