"""μ-law (G.711 PCMU) encoder and rhythm tone generator."""

import math

SAMPLE_RATE = 8000
ULAW_BIAS = 0x84
ULAW_CLIP = 32635
SILENCE = 0xFF


def linear_to_ulaw(sample: int) -> int:
    """Convert a 16-bit signed PCM sample to 8-bit μ-law."""
    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    if sample > ULAW_CLIP:
        sample = ULAW_CLIP
    sample += ULAW_BIAS

    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1

    mantissa = (sample >> (exponent + 3)) & 0x0F
    byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return byte


def _render_tone(freq: float, duration_s: float, amplitude: int) -> list[int]:
    """Render a sine tone as μ-law encoded samples."""
    n_samples = int(SAMPLE_RATE * duration_s)
    samples = []
    for i in range(n_samples):
        pcm = int(amplitude * math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE))
        samples.append(linear_to_ulaw(pcm))
    return samples


def generate_rhythm(duration_s: float = 60.0) -> bytes:
    """Pre-render a rhythmic audio buffer (120 BPM, 4/4 time).

    Beat 1: 440 Hz, 200ms, amplitude ~24000
    Beats 2-4: 330 Hz, 150ms, amplitude ~16000
    Silence fills the remainder of each beat.
    """
    beat_samples = int(SAMPLE_RATE * 0.5)  # 500ms per beat at 120 BPM

    beat1_tone = _render_tone(440.0, 0.2, 24000)
    beat1 = beat1_tone + [SILENCE] * (beat_samples - len(beat1_tone))

    weak_tone = _render_tone(330.0, 0.15, 16000)
    weak_beat = weak_tone + [SILENCE] * (beat_samples - len(weak_tone))

    measure = beat1 + weak_beat * 3  # 4 beats = 1 measure

    total_samples = int(SAMPLE_RATE * duration_s)
    buf = bytearray(total_samples)
    offset = 0
    while offset < total_samples:
        chunk = min(len(measure), total_samples - offset)
        buf[offset : offset + chunk] = measure[:chunk]
        offset += chunk

    return bytes(buf)
