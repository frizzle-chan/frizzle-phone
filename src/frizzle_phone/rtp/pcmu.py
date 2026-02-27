"""μ-law (G.711 PCMU) encoder."""

from frizzle_phone.synth import generate_rhythm_pcm

SAMPLE_RATE = 8000
ULAW_BIAS = 0x84
ULAW_CLIP = 32635
SILENCE = 0xFF


def _encode_ulaw(sample: int) -> int:
    """Encode a single 16-bit signed PCM sample to 8-bit μ-law."""
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
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _build_ulaw_table() -> bytes:
    """Pre-compute μ-law encoding for all 65536 possible 16-bit samples."""
    table = bytearray(65536)
    for i in range(65536):
        # Convert unsigned index to signed 16-bit
        sample = i if i < 32768 else i - 65536
        table[i] = _encode_ulaw(sample)
    return bytes(table)


_ULAW_TABLE: bytes = _build_ulaw_table()


def linear_to_ulaw(sample: int) -> int:
    """Convert a 16-bit signed PCM sample to 8-bit μ-law."""
    sample = max(-32768, min(32767, sample))
    return _ULAW_TABLE[sample & 0xFFFF]


def pcm_to_ulaw(samples: list[float], peak: float = 0.95) -> bytes:
    """Convert float PCM buffer to μ-law bytes with normalisation."""
    # Find actual peak for headroom
    max_val = max(abs(s) for s in samples) if samples else 1.0
    if max_val < 0.001:
        max_val = 1.0
    scale = peak * 32767.0 / max_val

    buf = bytearray(len(samples))
    table = _ULAW_TABLE
    for i, s in enumerate(samples):
        pcm = int(s * scale)
        pcm = max(-32768, min(32767, pcm))
        buf[i] = table[pcm & 0xFFFF]
    return bytes(buf)


def generate_rhythm(duration_s: float = 60.0) -> bytes:
    """Pre-render a techno beat at 130 BPM with Reese bass as μ-law bytes."""
    return pcm_to_ulaw(generate_rhythm_pcm(duration_s))
