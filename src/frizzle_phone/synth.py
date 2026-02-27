"""Telephone-quality audio synthesis — drums, bass, and mixing.

All generators return ``list[float]`` normalised roughly to [-1, 1] at 8 kHz.
Codec-agnostic: encode the output with your codec of choice (e.g. μ-law).
"""

import math
import random

# Must match the codec sample rate for correct pitch/tempo.
SAMPLE_RATE = 8000

# E Phrygian: E F G A B C D (rooted in bass octave)
_E_PHRYGIAN_FREQS = [
    82.41,  # E2
    87.31,  # F2
    98.00,  # G2
    110.00,  # A2
    123.47,  # B2
    130.81,  # C3
    146.83,  # D3
    164.81,  # E3
]


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def _highpass_noise(n: int) -> list[float]:
    """Highpass-filtered white noise (cutoff ~2kHz at 8kHz SR)."""
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
    """Sum of square waves at metallic ratios (TR-808 style)."""
    freqs = [800.0, 1340.0, 3200.0]
    out: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        val = 0.0
        for f in freqs:
            val += 1.0 if math.sin(2.0 * math.pi * f * t) >= 0 else -1.0
        out.append(val / len(freqs))
    return out


# ---------------------------------------------------------------------------
# Drum generators
# ---------------------------------------------------------------------------


def kick(duration_s: float = 0.08) -> list[float]:
    """Kick drum — sine sweep from ~150Hz down to ~50Hz."""
    n = int(SAMPLE_RATE * duration_s)
    out: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = max(0.0, 1.0 - t / duration_s)
        freq = 150.0 - 100.0 * (t / duration_s)
        out.append(env * math.sin(2.0 * math.pi * freq * t))
    return out


def hihat(duration_s: float = 0.04, decay: float = 8.0) -> list[float]:
    """Hi-hat — highpass noise + metallic tones with exponential decay.

    Closed: hihat() (short, fast decay).
    Open:   hihat(duration_s=0.12, decay=3.0).
    """
    n = int(SAMPLE_RATE * duration_s)
    noise = _highpass_noise(n)
    tones = _metallic_tones(n)
    out: list[float] = []
    for i in range(n):
        env = math.exp(-decay * i / n)
        out.append(env * (0.6 * noise[i] + 0.4 * tones[i]))
    return out


def snare(duration_s: float = 0.10) -> list[float]:
    """Snare — 200Hz body tone + noise burst."""
    n = int(SAMPLE_RATE * duration_s)
    out: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        env = max(0.0, 1.0 - t / duration_s)
        tone = math.sin(2.0 * math.pi * 200.0 * t)
        noise = random.random() * 2 - 1
        out.append(env * (0.4 * tone + 0.6 * noise))
    return out


def _saw(phase: float) -> float:
    """Band-limited-ish sawtooth from phase value."""
    return 2.0 * (phase - math.floor(phase + 0.5))


def reese_note(freq: float, duration_s: float) -> list[float]:
    """Reese bass — two detuned saws through a lowpass filter.

    Detune ±25 cents for the classic beating wobble,
    lowpass at ~650Hz to smooth it out.
    """
    n = int(SAMPLE_RATE * duration_s)
    detune = 2.0 ** (25.0 / 1200.0)  # ~1.0145
    f1 = freq * detune
    f2 = freq / detune

    # First-order lowpass at ~650Hz
    rc = 1.0 / (2.0 * math.pi * 650.0)
    dt = 1.0 / SAMPLE_RATE
    lp_alpha = dt / (rc + dt)

    prev_y = 0.0
    out: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        raw = 0.5 * (_saw(t * f1) + _saw(t * f2))
        # Amplitude envelope: quick attack, sustain, quick release
        if i < 20:
            env = i / 20.0
        elif i > n - 40:
            env = (n - i) / 40.0
        else:
            env = 1.0
        filtered = prev_y + lp_alpha * (raw * env - prev_y)
        prev_y = filtered
        out.append(filtered)
    return out


# ---------------------------------------------------------------------------
# Mixing helpers
# ---------------------------------------------------------------------------


def mix_into(dest: list[float], src: list[float], offset: int, gain: float) -> None:
    """Add src samples into dest at offset with gain."""
    for i, s in enumerate(src):
        pos = offset + i
        if pos < len(dest):
            dest[pos] += s * gain


# ---------------------------------------------------------------------------
# Bassline pattern generator
# ---------------------------------------------------------------------------


def generate_bass_pattern(sixteenth_samples: int, num_measures: int = 4) -> list[float]:
    """Generate a random syncopated Reese bassline in E Phrygian.

    Uses 16 sixteenth-note slots per measure. Syncopation bias:
    offbeats (the "and"s) are more likely to trigger than downbeats.
    """
    # Probability of a note on each sixteenth-note position (0-indexed)
    # Positions: 0=1, 1=1e, 2=1&, 3=1a, 4=2, 5=2e, 6=2&, 7=2a, ...
    probs = []
    for beat in range(4):
        probs.append(0.5 if beat == 0 else 0.3)  # downbeat
        probs.append(0.2)  # e
        probs.append(0.7)  # & (offbeat — high prob for syncopation)
        probs.append(0.3)  # a

    total_sixteenths = 16 * num_measures
    total_samples = sixteenth_samples * total_sixteenths
    bass_track: list[float] = [0.0] * total_samples

    # Favour root (E) and fifth (B) more heavily
    weighted_freqs = [
        _E_PHRYGIAN_FREQS[0],  # E — root
        _E_PHRYGIAN_FREQS[0],  # E
        _E_PHRYGIAN_FREQS[1],  # F
        _E_PHRYGIAN_FREQS[2],  # G
        _E_PHRYGIAN_FREQS[3],  # A
        _E_PHRYGIAN_FREQS[4],  # B — fifth
        _E_PHRYGIAN_FREQS[4],  # B
        _E_PHRYGIAN_FREQS[5],  # C
        _E_PHRYGIAN_FREQS[6],  # D
    ]

    pos = 0
    while pos < total_sixteenths:
        slot = pos % 16
        if random.random() < probs[slot]:
            # Pick note duration: 1-3 sixteenths (ties create longer notes)
            max_len = min(3, total_sixteenths - pos)
            note_len = random.randint(1, max_len)
            freq = random.choice(weighted_freqs)
            dur_s = note_len * sixteenth_samples / SAMPLE_RATE
            note = reese_note(freq, dur_s)
            mix_into(bass_track, note, pos * sixteenth_samples, 1.0)
            pos += note_len
        else:
            pos += 1

    return bass_track


# ---------------------------------------------------------------------------
# Main composition
# ---------------------------------------------------------------------------


def generate_rhythm_pcm(duration_s: float = 60.0) -> list[float]:
    """Pre-render a boots-and-cats techno beat at 130 BPM with Reese bass.

    Drum pattern per measure (4 beats, 8 eighth-notes):
      1     &     2     &     3     &     4     &
      K+H   OH    S+H   OH    K+H   OH    S+H   OH

    Plus a random syncopated Reese bassline in E Phrygian.

    Returns float PCM samples normalised roughly to [-1, 1].
    """
    beat_s = 60.0 / 130.0
    eighth_s = beat_s / 2.0
    sixteenth_s = beat_s / 4.0
    eighth_samples = int(SAMPLE_RATE * eighth_s)
    sixteenth_samples = int(SAMPLE_RATE * sixteenth_s)
    measure_samples = eighth_samples * 8

    total_samples = int(SAMPLE_RATE * duration_s)

    # Pre-render one-shot drum sounds (float PCM)
    kick_snd = kick()
    hihat_snd = hihat()
    open_hat = hihat(duration_s=0.12, decay=3.0)
    snare_snd = snare()

    # Build one measure of drums
    drum_measure: list[float] = [0.0] * measure_samples
    for beat in range(4):
        on_offset = beat * 2 * eighth_samples
        off_offset = on_offset + eighth_samples

        # On-beat: kick or snare + closed hi-hat
        if beat % 2 == 0:
            mix_into(drum_measure, kick_snd, on_offset, 1.0)
        else:
            mix_into(drum_measure, snare_snd, on_offset, 0.9)
        mix_into(drum_measure, hihat_snd, on_offset, 0.6)

        # Off-beat: open hi-hat
        mix_into(drum_measure, open_hat, off_offset, 0.55)

    # Tile drum measures across full duration
    drums: list[float] = [0.0] * total_samples
    offset = 0
    while offset < total_samples:
        chunk = min(measure_samples, total_samples - offset)
        drums[offset : offset + chunk] = drum_measure[:chunk]
        offset += measure_samples

    # Generate bass (4-measure phrases, tiled)
    phrase_len = sixteenth_samples * 16 * 4
    bass: list[float] = [0.0] * total_samples
    offset = 0
    while offset < total_samples:
        bass_phrase = generate_bass_pattern(sixteenth_samples, num_measures=4)
        chunk = min(phrase_len, total_samples - offset)
        bass[offset : offset + chunk] = bass_phrase[:chunk]
        offset += phrase_len

    # Mix drums + bass
    return [d + 0.7 * b for d, b in zip(drums, bass, strict=True)]
