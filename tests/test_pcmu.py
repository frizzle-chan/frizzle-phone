import struct

from frizzle_phone.rtp.pcmu import pcm16_to_ulaw, pcm_to_ulaw, ulaw_to_pcm

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


def test_ulaw_to_pcm_length():
    """160 ulaw bytes → 320 PCM bytes (2 bytes per sample)."""
    result = ulaw_to_pcm(b"\xff" * 160)
    assert len(result) == 320


def test_ulaw_to_pcm_silence():
    """ulaw 0xFF (silence) decodes to near-zero PCM."""
    result = ulaw_to_pcm(bytes([0xFF]))
    sample = struct.unpack_from("<h", result)[0]
    assert abs(sample) < 10


def test_ulaw_decode_encode_roundtrip():
    """int16 → ulaw → decode → int16, within G.711 quantization error."""
    test_values = [0, 100, -100, 1000, -1000, 10000, -10000, 32000, -32000]
    for val in test_values:
        pcm_bytes = struct.pack("<h", val)
        encoded = pcm16_to_ulaw(pcm_bytes)
        decoded = ulaw_to_pcm(encoded)
        result = struct.unpack_from("<h", decoded)[0]
        assert abs(result - val) < max(abs(val) * 0.05, 16), f"{val} → {result}"


def test_pcm16_to_ulaw_length():
    """320 bytes int16 PCM → 160 bytes ulaw."""
    result = pcm16_to_ulaw(b"\x00" * 320)
    assert len(result) == 160


def test_pcm16_to_ulaw_silence():
    """int16 zeros → ulaw silence (0xFF)."""
    result = pcm16_to_ulaw(b"\x00" * 4)
    assert all(b == 0xFF for b in result)
