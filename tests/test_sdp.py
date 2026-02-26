from frizzle_phone.sip.sdp import build_sdp_answer, parse_sdp_offer


def test_sdp_contains_connection():
    sdp = build_sdp_answer("192.168.1.100")
    assert "c=IN IP4 192.168.1.100" in sdp


def test_sdp_codec_pcmu():
    sdp = build_sdp_answer("192.168.1.100", rtp_port=10000)
    assert "m=audio 10000 RTP/AVP 0" in sdp


def test_sdp_ptime():
    sdp = build_sdp_answer("192.168.1.100")
    assert "a=ptime:20" in sdp


def test_parse_sdp_offer_basic():
    sdp = (
        "v=0\r\n"
        "o=alice 123 456 IN IP4 10.0.0.1\r\n"
        "s=Session\r\n"
        "c=IN IP4 10.0.0.1\r\n"
        "t=0 0\r\n"
        "m=audio 4000 RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
    )
    offer = parse_sdp_offer(sdp)
    assert offer.audio_port == 4000
    assert offer.connection_address == "10.0.0.1"


def test_parse_sdp_offer_missing_media():
    sdp = "v=0\r\nc=IN IP4 10.0.0.1\r\n"
    offer = parse_sdp_offer(sdp)
    assert offer.audio_port == 0
    assert offer.connection_address == "10.0.0.1"


def test_parse_sdp_offer_connection_with_subnet():
    sdp = "v=0\r\nc=IN IP4 224.2.36.42/127\r\nm=audio 5004 RTP/AVP 0\r\n"
    offer = parse_sdp_offer(sdp)
    assert offer.connection_address == "224.2.36.42"
    assert offer.audio_port == 5004
