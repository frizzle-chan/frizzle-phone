from frizzle_phone.sip.message import build_response, parse_request


def _make_register() -> bytes:
    return (
        b"REGISTER sip:example.com SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bK776\r\n"
        b"From: <sip:alice@example.com>;tag=abc123\r\n"
        b"To: <sip:alice@example.com>\r\n"
        b"Call-ID: reg-001@10.0.0.1\r\n"
        b"CSeq: 1 REGISTER\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )


def _make_invite_with_sdp() -> bytes:
    sdp = "v=0\r\no=- 0 0 IN IP4 10.0.0.1\r\n"
    body = sdp.encode()
    return (
        b"INVITE sip:bob@example.com SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bK999\r\n"
        b"From: <sip:alice@example.com>;tag=xyz789\r\n"
        b"To: <sip:bob@example.com>\r\n"
        b"Call-ID: invite-001@10.0.0.1\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Content-Type: application/sdp\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"\r\n" + body
    )


def test_parse_register():
    msg = parse_request(_make_register())
    assert msg.method == "REGISTER"
    assert msg.uri == "sip:example.com"
    assert msg.header("Call-ID") == "reg-001@10.0.0.1"
    assert msg.header("CSeq") == "1 REGISTER"


def test_parse_invite_with_sdp():
    msg = parse_request(_make_invite_with_sdp())
    assert msg.method == "INVITE"
    assert msg.body != ""
    assert "v=0" in msg.body


def test_build_200_ok():
    msg = parse_request(_make_register())
    response = build_response(msg, 200, "OK")
    text = response.decode()
    assert text.startswith("SIP/2.0 200 OK\r\n")
    assert "Via:" in text
    assert "Call-ID:" in text
    assert "CSeq:" in text


def test_build_response_with_body():
    msg = parse_request(_make_invite_with_sdp())
    body = "v=0\r\no=test\r\n"
    response = build_response(msg, 200, "OK", body=body)
    text = response.decode()
    assert "Content-Type: application/sdp" in text
    assert f"Content-Length: {len(body)}" in text
    assert text.endswith(body)


def test_headers_case_insensitive():
    msg = parse_request(_make_register())
    assert msg.header("via") is not None
    assert msg.header("VIA") is not None
    assert msg.header("Via") is not None
