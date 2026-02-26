from frizzle_phone.sip.sdp import build_sdp_answer


def test_sdp_contains_connection():
    sdp = build_sdp_answer("192.168.1.100")
    assert "c=IN IP4 192.168.1.100" in sdp


def test_sdp_codec_pcmu():
    sdp = build_sdp_answer("192.168.1.100", rtp_port=10000)
    assert "m=audio 10000 RTP/AVP 0" in sdp


def test_sdp_ptime():
    sdp = build_sdp_answer("192.168.1.100")
    assert "a=ptime:20" in sdp
