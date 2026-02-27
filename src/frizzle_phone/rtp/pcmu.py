"""μ-law (G.711 PCMU) encoder and rhythm tone generator."""

import math
import random

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


def _render_pcm(n_samples: int, gen: object) -> list[int]:
    """Render PCM samples from a generator-like callable to μ-law."""
    # gen is a callable(i) -> int
    return [linear_to_ulaw(gen(i)) for i in range(n_samples)]  # type: ignore[operator]


def _render_noise(duration_s: float, amplitude: int) -> list[int]:
    """White noise burst for hi-hat / snare texture."""
    n = int(SAMPLE_RATE * duration_s)
    return [linear_to_ulaw(random.randint(-amplitude, amplitude)) for _ in range(n)]


def _render_kick(duration_s: float = 0.08) -> list[int]:
    """Synthesize a kick drum — sine sweep from ~150Hz down to ~50Hz."""
    n = int(SAMPLE_RATE * duration_s)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = max(0.0, 1.0 - t / duration_s)  # linear decay
        freq = 150.0 - 100.0 * (t / duration_s)  # pitch sweep down
        pcm = int(28000 * env * math.sin(2.0 * math.pi * freq * t))
        samples.append(linear_to_ulaw(pcm))
    return samples


def _highpass_noise(n: int) -> list[float]:
    """Generate highpass-filtered white noise (cutoff ~2kHz at 8kHz SR).

    Uses a first-order IIR highpass filter to remove low frequencies,
    giving the noise a brighter, more metallic character.
    """
    # First-order highpass: y[n] = a * (y[n-1] + x[n] - x[n-1])
    # cutoff ~2kHz at 8kHz sample rate
    rc = 1.0 / (2.0 * math.pi * 2000.0)
    dt = 1.0 / SAMPLE_RATE
    alpha = rc / (rc + dt)

    prev_x = 0.0
    prev_y = 0.0
    out: list[float] = []
    for _ in range(n):
        x = random.random() * 2 - 1
        y = alpha * (prev_y + x - prev_x)
        prev_x = x
        prev_y = y
        out.append(y)
    return out


def _metallic_tones(n: int) -> list[float]:
    """Sum of detuned square waves at metallic ratios (TR-808 style)."""
    # 808 uses 6 square oscillators; we use 3 at high frequencies
    freqs = [800.0, 1340.0, 3200.0]
    out: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        val = 0.0
        for f in freqs:
            # Square wave via sign of sine
            val += 1.0 if math.sin(2.0 * math.pi * f * t) >= 0 else -1.0
        out.append(val / len(freqs))
    return out


def _render_hihat(duration_s: float = 0.04) -> list[int]:
    """Closed hi-hat — highpass noise + metallic tones, fast exp decay."""
    n = int(SAMPLE_RATE * duration_s)
    noise = _highpass_noise(n)
    tones = _metallic_tones(n)
    samples = []
    for i in range(n):
        env = math.exp(-8.0 * i / n)  # fast exponential decay
        mixed = 0.6 * noise[i] + 0.4 * tones[i]
        pcm = int(18000 * env * mixed)
        samples.append(linear_to_ulaw(pcm))
    return samples


def _render_open_hihat(duration_s: float = 0.12) -> list[int]:
    """Open hi-hat — same character, slower decay."""
    n = int(SAMPLE_RATE * duration_s)
    noise = _highpass_noise(n)
    tones = _metallic_tones(n)
    samples = []
    for i in range(n):
        env = math.exp(-3.0 * i / n)  # slower exponential decay
        mixed = 0.6 * noise[i] + 0.4 * tones[i]
        pcm = int(16000 * env * mixed)
        samples.append(linear_to_ulaw(pcm))
    return samples


def _render_snare(duration_s: float = 0.10) -> list[int]:
    """Snare — mix of ~200Hz body tone and noise burst."""
    n = int(SAMPLE_RATE * duration_s)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = max(0.0, 1.0 - t / duration_s)
        tone = math.sin(2.0 * math.pi * 200.0 * t)
        noise = random.random() * 2 - 1
        pcm = int(22000 * env * (0.4 * tone + 0.6 * noise))
        samples.append(linear_to_ulaw(pcm))
    return samples


def _pad(samples: list[int], total: int) -> list[int]:
    """Pad or truncate sample list to exact length."""
    if len(samples) >= total:
        return samples[:total]
    return samples + [SILENCE] * (total - len(samples))


def generate_rhythm(duration_s: float = 60.0) -> bytes:
    """Pre-render a boots-and-cats techno beat at 130 BPM.

    Pattern per measure (4 beats, 8 eighth-notes):
      1     &     2     &     3     &     4     &
      K+H   OH    S+H   OH    K+H   OH    S+H   OH

    K = kick, S = snare, H = closed hi-hat, OH = open hi-hat
    """
    beat_s = 60.0 / 130.0  # ~0.4615s per beat
    eighth_s = beat_s / 2.0
    eighth_samples = int(SAMPLE_RATE * eighth_s)

    # Pre-render one-shot sounds
    kick = _render_kick()
    hihat = _render_hihat()
    open_hat = _render_open_hihat()
    snare = _render_snare()

    # "boots" = kick + closed hi-hat
    boots: list[int] = []
    for i in range(max(len(kick), len(hihat))):
        boots.append(kick[i] if i < len(kick) else hihat[i])
    boots_padded = _pad(boots, eighth_samples)

    # snare + closed hi-hat
    snare_hit: list[int] = []
    for i in range(max(len(snare), len(hihat))):
        snare_hit.append(snare[i] if i < len(snare) else hihat[i])
    snare_padded = _pad(snare_hit, eighth_samples)

    # "cats" = open hi-hat
    cats_padded = _pad(open_hat, eighth_samples)

    # One measure: K+H, OH, S+H, OH, K+H, OH, S+H, OH
    measure: list[int] = []
    for beat in range(4):
        if beat % 2 == 0:
            measure.extend(boots_padded)
        else:
            measure.extend(snare_padded)
        measure.extend(cats_padded)

    total_samples = int(SAMPLE_RATE * duration_s)
    buf = bytearray(total_samples)
    offset = 0
    while offset < total_samples:
        chunk = min(len(measure), total_samples - offset)
        buf[offset : offset + chunk] = measure[:chunk]
        offset += chunk

    return bytes(buf)
