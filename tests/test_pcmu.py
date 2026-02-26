from frizzle_phone.rtp.pcmu import SILENCE, generate_rhythm, linear_to_ulaw


def test_ulaw_silence():
    assert linear_to_ulaw(0) == SILENCE


def test_ulaw_positive_max():
    result = linear_to_ulaw(32767)
    assert 0 <= result <= 255
    # In complemented μ-law, bit 7 set = positive
    assert result & 0x80


def test_ulaw_negative():
    result = linear_to_ulaw(-1000)
    # In complemented μ-law, bit 7 clear = negative
    assert not (result & 0x80)


def test_rhythm_length():
    buf = generate_rhythm(60.0)
    assert len(buf) == 60 * 8000


def test_rhythm_starts_with_tone():
    buf = generate_rhythm(1.0)
    # Early samples should contain tone (not all silence)
    assert any(b != SILENCE for b in buf[:20])
