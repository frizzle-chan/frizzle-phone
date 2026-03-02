"""μ-law (G.711 PCMU) encoder/decoder."""

import numpy as np

SAMPLE_RATE = 8000
ULAW_BIAS = 0x84
ULAW_CLIP = 32635


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


def _build_ulaw_decode_table() -> list[int]:
    """Pre-compute signed int16 PCM for all 256 μ-law input values."""
    table = [0] * 256
    for ulaw_byte in range(256):
        # ITU-T G.711 standard decode
        inv = ~ulaw_byte & 0xFF
        sign = inv & 0x80
        exponent = (inv >> 4) & 0x07
        mantissa = inv & 0x0F
        sample = ((mantissa << 3) + ULAW_BIAS) << exponent
        sample -= ULAW_BIAS
        if sign:
            sample = -sample
        table[ulaw_byte] = max(-32768, min(32767, sample))
    return table


_ULAW_DECODE_TABLE: list[int] = _build_ulaw_decode_table()

_ULAW_DECODE_NP: np.ndarray = np.array(_ULAW_DECODE_TABLE, dtype=np.int16)
_ULAW_TABLE_NP: np.ndarray = np.frombuffer(_ULAW_TABLE, dtype=np.uint8)


def ulaw_to_pcm(data: bytes) -> bytes:
    """Decode μ-law bytes to signed 16-bit little-endian PCM bytes."""
    return _ULAW_DECODE_NP[np.frombuffer(data, dtype=np.uint8)].tobytes()


def pcm16_to_ulaw(data: bytes) -> bytes:
    """Encode signed 16-bit little-endian PCM bytes to μ-law."""
    return bytes(_ULAW_TABLE_NP[np.frombuffer(data, dtype=np.int16).view(np.uint16)])


def pcm16_arr_to_ulaw(samples: np.ndarray) -> bytes:
    """Encode int16 ndarray to μ-law bytes (avoids bytes→ndarray round-trip)."""
    return bytes(_ULAW_TABLE_NP[samples.view(np.uint16)])


def pcm_to_ulaw(samples: list[float], peak: float = 0.95) -> bytes:
    """Convert float PCM buffer to μ-law bytes with normalisation."""
    arr = np.asarray(samples, dtype=np.float64)
    max_val = float(np.max(np.abs(arr))) if len(arr) > 0 else 1.0
    if max_val < 0.001:
        max_val = 1.0
    scale = peak * 32767.0 / max_val
    pcm = np.clip(arr * scale, -32768, 32767).astype(np.int16)
    return bytes(_ULAW_TABLE_NP[pcm.view(np.uint16)])
