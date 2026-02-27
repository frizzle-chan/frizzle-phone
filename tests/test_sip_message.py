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
    response = build_response(msg, 200, "OK", to_tag="testtag")
    text = response.decode()
    assert text.startswith("SIP/2.0 200 OK\r\n")
    assert "Via:" in text
    assert "Call-ID:" in text
    assert "CSeq:" in text


def test_build_response_with_body():
    msg = parse_request(_make_invite_with_sdp())
    body = "v=0\r\no=test\r\n"
    response = build_response(msg, 200, "OK", body=body, to_tag="t1")
    text = response.decode()
    assert "Content-Type: application/sdp" in text
    body_bytes = body.encode("utf-8")
    assert f"Content-Length: {len(body_bytes)}" in text
    assert text.endswith(body)


def test_headers_case_insensitive():
    msg = parse_request(_make_register())
    assert msg.header("via") is not None
    assert msg.header("VIA") is not None
    assert msg.header("Via") is not None


def test_multi_via_preserved():
    """Multiple Via headers should all be preserved in order."""
    raw = (
        b"INVITE sip:bob@example.com SIP/2.0\r\n"
        b"Via: SIP/2.0/UDP proxy.example.com;branch=z9hG4bKaaa\r\n"
        b"Via: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bKbbb\r\n"
        b"From: <sip:alice@example.com>;tag=abc\r\n"
        b"To: <sip:bob@example.com>\r\n"
        b"Call-ID: multi-via@test\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"Content-Length: 0\r\n"
        b"\r\n"
    )
    msg = parse_request(raw)
    vias = msg.header_values("Via")
    assert len(vias) == 2
    assert vias[0] == "SIP/2.0/UDP proxy.example.com;branch=z9hG4bKaaa"
    assert vias[1] == "SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bKbbb"

    # Response should mirror both Vias in order
    resp = build_response(msg, 200, "OK", to_tag="t1")
    text = resp.decode()
    via_lines = [line for line in text.split("\r\n") if line.startswith("Via:")]
    assert len(via_lines) == 2
    assert via_lines[0] == "Via: SIP/2.0/UDP proxy.example.com;branch=z9hG4bKaaa"
    assert via_lines[1] == "Via: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bKbbb"


def test_to_tag_explicit():
    """to_tag kwarg adds tag to To header."""
    msg = parse_request(_make_register())
    resp = build_response(msg, 200, "OK", to_tag="mytag42")
    text = resp.decode()
    assert ";tag=mytag42" in text


def test_no_tag_when_to_tag_none():
    """When to_tag is None, no tag is added to To header."""
    msg = parse_request(_make_register())
    resp = build_response(msg, 100, "Trying")
    text = resp.decode()
    to_line = [line for line in text.split("\r\n") if line.startswith("To:")][0]
    assert ";tag=" not in to_line


def test_extra_headers():
    """extra_headers kwarg appends additional headers."""
    msg = parse_request(_make_register())
    resp = build_response(
        msg,
        200,
        "OK",
        to_tag="t1",
        extra_headers=[("Contact", "<sip:server@10.0.0.1>"), ("Allow", "INVITE, BYE")],
    )
    text = resp.decode()
    assert "Contact: <sip:server@10.0.0.1>" in text
    assert "Allow: INVITE, BYE" in text


def test_content_length_byte_count():
    """Content-Length should be byte length, not character length."""
    msg = parse_request(_make_register())
    # Body with multi-byte characters
    body = "v=0\r\n\u00e9\r\n"  # é is 2 bytes in UTF-8
    resp = build_response(msg, 200, "OK", body=body, to_tag="t1")
    text = resp.decode()
    byte_len = len(body.encode("utf-8"))
    assert byte_len != len(body)  # sanity check: byte len differs from char len
    assert f"Content-Length: {byte_len}" in text
