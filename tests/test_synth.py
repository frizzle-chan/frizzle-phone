from frizzle_phone.synth import (
    SAMPLE_RATE,
    _saw,
    generate_bass_pattern,
    generate_beeps_pcm,
    generate_rhythm_pcm,
    hihat,
    kick,
    mix_into,
    reese_note,
    snare,
)


def test_kick_length_and_range():
    samples = kick(0.08)
    assert len(samples) == int(SAMPLE_RATE * 0.08)
    assert all(-1.5 <= s <= 1.5 for s in samples)


def test_hihat_length():
    samples = hihat(0.04)
    assert len(samples) == int(SAMPLE_RATE * 0.04)


def test_hihat_open():
    closed = hihat()
    opened = hihat(duration_s=0.12, decay=3.0)
    assert len(opened) > len(closed)


def test_snare_length_and_range():
    samples = snare(0.10)
    assert len(samples) == int(SAMPLE_RATE * 0.10)
    assert all(-2.0 <= s <= 2.0 for s in samples)


def test_saw_range():
    # Sawtooth should stay within [-1, 1]
    for phase in [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.3]:
        assert -1.0 <= _saw(phase) <= 1.0


def test_reese_note_length():
    samples = reese_note(82.41, 0.1)
    assert len(samples) == int(SAMPLE_RATE * 0.1)


def test_mix_into_adds_with_gain():
    dest = [0.0, 0.0, 0.0, 0.0, 0.0]
    src = [1.0, 2.0]
    mix_into(dest, src, 1, 0.5)
    assert dest == [0.0, 0.5, 1.0, 0.0, 0.0]


def test_mix_into_clips_at_end():
    dest = [0.0, 0.0, 0.0]
    src = [1.0, 1.0, 1.0, 1.0]
    mix_into(dest, src, 2, 1.0)
    assert dest == [0.0, 0.0, 1.0]


def test_generate_bass_pattern_length():
    sixteenth_samples = 100
    pattern = generate_bass_pattern(sixteenth_samples, num_measures=2)
    assert len(pattern) == sixteenth_samples * 16 * 2


def test_generate_rhythm_pcm_length():
    pcm = generate_rhythm_pcm(1.0)
    assert len(pcm) == SAMPLE_RATE


def test_generate_beeps_pcm_length():
    samples = generate_beeps_pcm()
    # 3 beeps × 200ms + 2 gaps × 200ms = 1000ms
    expected = int(SAMPLE_RATE * 0.2) * 3 + int(SAMPLE_RATE * 0.2) * 2
    assert len(samples) == expected


def test_generate_beeps_pcm_has_silence_gaps():
    samples = generate_beeps_pcm()
    beep_n = int(SAMPLE_RATE * 0.2)
    gap_n = int(SAMPLE_RATE * 0.2)
    # Middle of first gap should be silent
    gap_start = beep_n
    mid_gap = gap_start + gap_n // 2
    assert samples[mid_gap] == 0.0


def test_generate_beeps_pcm_range():
    samples = generate_beeps_pcm()
    assert all(-1.1 <= s <= 1.1 for s in samples)


def test_generate_rhythm_pcm_not_silent():
    pcm = generate_rhythm_pcm(1.0)
    assert any(abs(s) > 0.01 for s in pcm[:200])
