from frizzle_phone.sip.message import (
    build_request,
    build_response,
    extract_extension,
    parse_message,
)


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
    msg = parse_message(_make_register())
    assert msg.method == "REGISTER"
    assert msg.uri == "sip:example.com"
    assert msg.header("Call-ID") == "reg-001@10.0.0.1"
    assert msg.header("CSeq") == "1 REGISTER"


def test_parse_invite_with_sdp():
    msg = parse_message(_make_invite_with_sdp())
    assert msg.method == "INVITE"
    assert msg.body != ""
    assert "v=0" in msg.body


def test_build_200_ok():
    msg = parse_message(_make_register())
    response = build_response(msg, 200, "OK", to_tag="testtag")
    text = response.decode()
    assert text.startswith("SIP/2.0 200 OK\r\n")
    assert "Via:" in text
    assert "Call-ID:" in text
    assert "CSeq:" in text


def test_build_response_with_body():
    msg = parse_message(_make_invite_with_sdp())
    body = "v=0\r\no=test\r\n"
    response = build_response(msg, 200, "OK", body=body, to_tag="t1")
    text = response.decode()
    assert "Content-Type: application/sdp" in text
    body_bytes = body.encode("utf-8")
    assert f"Content-Length: {len(body_bytes)}" in text
    assert text.endswith(body)


def test_headers_case_insensitive():
    msg = parse_message(_make_register())
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
    msg = parse_message(raw)
    vias = [v for k, v in msg.headers if k.lower() == "via"]
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
    msg = parse_message(_make_register())
    resp = build_response(msg, 200, "OK", to_tag="mytag42")
    text = resp.decode()
    assert ";tag=mytag42" in text


def test_no_tag_when_to_tag_none():
    """When to_tag is None, no tag is added to To header."""
    msg = parse_message(_make_register())
    resp = build_response(msg, 100, "Trying")
    text = resp.decode()
    to_line = [line for line in text.split("\r\n") if line.startswith("To:")][0]
    assert ";tag=" not in to_line


def test_extra_headers():
    """extra_headers kwarg appends additional headers."""
    msg = parse_message(_make_register())
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


def test_compact_header_forms():
    """Compact header abbreviations (RFC 3261 §7.3.1) are normalized."""
    raw = (
        b"INVITE sip:bob@example.com SIP/2.0\r\n"
        b"v: SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bK123\r\n"
        b"f: <sip:alice@example.com>;tag=abc\r\n"
        b"t: <sip:bob@example.com>\r\n"
        b"i: compact-test@10.0.0.1\r\n"
        b"CSeq: 1 INVITE\r\n"
        b"l: 0\r\n"
        b"\r\n"
    )
    msg = parse_message(raw)
    assert msg.header("Via") == "SIP/2.0/UDP 10.0.0.1:5060;branch=z9hG4bK123"
    from_val = msg.header("From")
    assert from_val is not None
    assert "alice" in from_val
    assert msg.header("To") is not None
    assert msg.header("Call-ID") == "compact-test@10.0.0.1"
    assert msg.header("Content-Length") == "0"


def test_content_length_byte_count():
    """Content-Length should be byte length, not character length."""
    msg = parse_message(_make_register())
    # Body with multi-byte characters
    body = "v=0\r\n\u00e9\r\n"  # é is 2 bytes in UTF-8
    resp = build_response(msg, 200, "OK", body=body, to_tag="t1")
    text = resp.decode()
    byte_len = len(body.encode("utf-8"))
    assert byte_len != len(body)  # sanity check: byte len differs from char len
    assert f"Content-Length: {byte_len}" in text


def test_build_response_custom_content_type():
    """content_type parameter overrides the default application/sdp."""
    msg = parse_message(_make_register())
    resp = build_response(
        msg, 200, "OK", body="hello", to_tag="t1", content_type="text/plain"
    )
    text = resp.decode()
    assert "Content-Type: text/plain" in text
    assert "application/sdp" not in text


# --- build_request tests ---


def test_build_request_basic():
    """build_request produces a valid SIP request line and headers."""
    data = build_request(
        "BYE",
        "sip:alice@10.0.0.1",
        headers=[
            ("Via", "SIP/2.0/UDP 10.0.0.2:5060;branch=z9hG4bK001"),
            ("From", "<sip:bob@10.0.0.2>;tag=aaa"),
            ("To", "<sip:alice@10.0.0.1>;tag=bbb"),
            ("Call-ID", "test-call-001"),
            ("CSeq", "1 BYE"),
            ("Max-Forwards", "70"),
        ],
    )
    text = data.decode()
    assert text.startswith("BYE sip:alice@10.0.0.1 SIP/2.0\r\n")
    assert "Via: SIP/2.0/UDP 10.0.0.2:5060;branch=z9hG4bK001" in text
    assert "Call-ID: test-call-001" in text
    assert "Content-Length: 0" in text


def test_build_request_with_body():
    """build_request includes Content-Type and correct Content-Length for body."""
    body = "v=0\r\no=test\r\n"
    data = build_request(
        "INVITE",
        "sip:bob@10.0.0.1",
        headers=[
            ("Via", "SIP/2.0/UDP 10.0.0.2:5060;branch=z9hG4bK002"),
            ("Call-ID", "invite-001"),
            ("CSeq", "1 INVITE"),
        ],
        body=body,
    )
    text = data.decode()
    assert text.startswith("INVITE sip:bob@10.0.0.1 SIP/2.0\r\n")
    assert "Content-Type: application/sdp" in text
    body_bytes = body.encode("utf-8")
    assert f"Content-Length: {len(body_bytes)}" in text
    assert text.endswith(body)


def test_build_request_custom_content_type():
    """build_request respects custom content_type."""
    data = build_request(
        "MESSAGE",
        "sip:bob@10.0.0.1",
        headers=[("CSeq", "1 MESSAGE")],
        body="hello",
        content_type="text/plain",
    )
    text = data.decode()
    assert "Content-Type: text/plain" in text
    assert "application/sdp" not in text


def test_extract_extension_basic():
    assert extract_extension("sip:111@10.0.0.2") == "111"


def test_extract_extension_with_port():
    assert extract_extension("sip:frizzle@10.0.0.2:5060") == "frizzle"


def test_extract_extension_no_user():
    assert extract_extension("sip:10.0.0.2") == "10.0.0.2"


def test_build_request_no_body():
    """build_request with no body omits Content-Type."""
    data = build_request(
        "BYE",
        "sip:alice@10.0.0.1",
        headers=[("CSeq", "1 BYE")],
    )
    text = data.decode()
    assert "Content-Type" not in text
    assert "Content-Length: 0" in text
