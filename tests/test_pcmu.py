from frizzle_phone.rtp.pcmu import pcm_to_ulaw

ULAW_SILENCE = 0xFF


def test_pcm_to_ulaw_length():
    samples = [0.0, 0.5, -0.5, 1.0, -1.0]
    result = pcm_to_ulaw(samples)
    assert len(result) == len(samples)


def test_pcm_to_ulaw_silence():
    result = pcm_to_ulaw([0.0, 0.0, 0.0])
    assert all(b == ULAW_SILENCE for b in result)


def test_pcm_to_ulaw_nonsilent():
    result = pcm_to_ulaw([0.0, 0.8, -0.8])
    assert result[1] != ULAW_SILENCE
    assert result[2] != ULAW_SILENCE
