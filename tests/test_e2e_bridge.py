"""E2E test: SIP INVITE → Discord bridge → 5-speaker RTP audio delivery."""

from __future__ import annotations

import asyncio
from functools import partial
from unittest.mock import MagicMock

import aiosqlite
import numpy as np
import pytest
import pytest_asyncio

from frizzle_phone.rtp.pcmu import ulaw_to_pcm
from frizzle_phone.sip.message import parse_message
from frizzle_phone.sip.server import start_server
from tests.audio_helpers import pcm_to_wav, wav_samples_check
from tests.fake_voice import CHORD_FREQS, FakeVoiceRecvClient, sine_tone_speakers
from tests.rtp_helpers import RtpCollector, parse_rtp_payload

# --- Constants ---
NUM_SPEAKERS = 5
TICKS = 200  # 4s of audio
SAMPLE_RATE_8K = 8000
TEST_GUILD_ID = 1234
TEST_CHANNEL_ID = 5678


# --- Helpers ---


class _ClientProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.queue.put_nowait(data)


async def _recv(queue: asyncio.Queue[bytes], timeout: float = 2.0) -> bytes:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def _recv_responses(
    queue: asyncio.Queue[bytes], n: int, timeout: float = 2.0
) -> list[bytes]:
    return [await _recv(queue, timeout=timeout) for _ in range(n)]


class _FakeVoiceConnector:
    """Returns a pre-built FakeVoiceRecvClient for the test guild/channel."""

    def __init__(self, fake_vc: FakeVoiceRecvClient) -> None:
        self._fake_vc = fake_vc

    async def connect(self, guild_id: int, channel_id: int) -> FakeVoiceRecvClient:
        return self._fake_vc


def _build_invite(
    server_port: int,
    client_port: int,
    rtp_port: int,
    *,
    call_id: str = "e2e-bridge",
    branch: str = "z9hG4bKbr1",
) -> bytes:
    sdp = (
        "v=0\r\n"
        "o=test 0 0 IN IP4 127.0.0.1\r\n"
        "s=test\r\n"
        "c=IN IP4 127.0.0.1\r\n"
        "t=0 0\r\n"
        f"m=audio {rtp_port} RTP/AVP 0\r\n"
    )
    body = sdp.encode()
    lines = [
        f"INVITE sip:discord@127.0.0.1:{server_port} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch={branch}",
        "From: <sip:test@127.0.0.1>;tag=fromtag1",
        "To: <sip:discord@127.0.0.1>",
        f"Call-ID: {call_id}",
        "CSeq: 1 INVITE",
        f"Contact: <sip:test@127.0.0.1:{client_port}>",
        "Max-Forwards: 70",
        "Content-Type: application/sdp",
        f"Content-Length: {len(body)}",
        "",
        "",
    ]
    return "\r\n".join(lines).encode() + body


def _build_ack(
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-bridge",
    branch: str = "z9hG4bKbr2",
) -> bytes:
    lines = [
        f"ACK sip:frizzle@127.0.0.1:{server_port} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch={branch}",
        "From: <sip:test@127.0.0.1>;tag=fromtag1",
        "To: <sip:discord@127.0.0.1>",
        f"Call-ID: {call_id}",
        "CSeq: 1 ACK",
        f"Contact: <sip:test@127.0.0.1:{client_port}>",
        "Max-Forwards: 70",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


def _build_bye(
    server_port: int,
    client_port: int,
    *,
    call_id: str = "e2e-bridge",
    branch: str = "z9hG4bKbr3",
) -> bytes:
    lines = [
        f"BYE sip:frizzle@127.0.0.1:{server_port} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch={branch}",
        "From: <sip:test@127.0.0.1>;tag=fromtag1",
        "To: <sip:discord@127.0.0.1>",
        f"Call-ID: {call_id}",
        "CSeq: 2 BYE",
        f"Contact: <sip:test@127.0.0.1:{client_port}>",
        "Max-Forwards: 70",
        "Content-Length: 0",
        "",
        "",
    ]
    return "\r\n".join(lines).encode()


# --- Fixtures ---


@pytest_asyncio.fixture
async def discord_db(db: aiosqlite.Connection) -> aiosqlite.Connection:
    """DB seeded with a discord extension pointing to test guild/channel."""
    await db.execute(
        "INSERT INTO discord_extensions (extension, guild_id, channel_id)"
        " VALUES ('discord', ?, ?)",
        (TEST_GUILD_ID, TEST_CHANNEL_ID),
    )
    await db.commit()
    return db


# --- Tests ---


@pytest.mark.asyncio
async def test_five_speaker_bridge_audio(
    discord_db: aiosqlite.Connection, file_regression
) -> None:
    """Full E2E: INVITE → bridge with 5 sine-tone speakers → verify RTP audio."""
    loop = asyncio.get_running_loop()

    # 1. Set up RTP collector ("the phone")
    collector = RtpCollector()
    rtp_recv_transport, _ = await loop.create_datagram_endpoint(
        lambda: collector, local_addr=("127.0.0.1", 0)
    )
    rtp_port = rtp_recv_transport.get_extra_info("sockname")[1]

    # 2. Set up fake voice client with 5 speakers
    speakers = sine_tone_speakers(n_ticks=TICKS)
    fake_vc = FakeVoiceRecvClient(speakers)
    connector = _FakeVoiceConnector(fake_vc)

    # 3. Start SIP server with fake voice connector
    sip_transport, server = await start_server(
        "127.0.0.1",
        0,
        server_ip="127.0.0.1",
        db=discord_db,
        audio_buffers={},
        bot=MagicMock(),
        voice_connector=connector,
    )
    _, server_port = sip_transport.get_extra_info("sockname")

    # 4. SIP client
    sip_proto = _ClientProtocol()
    sip_client_transport, _ = await loop.create_datagram_endpoint(
        lambda: sip_proto, remote_addr=("127.0.0.1", server_port)
    )
    client_port = sip_client_transport.get_extra_info("sockname")[1]

    try:
        # 5. INVITE with SDP pointing to our RTP collector
        sip_client_transport.sendto(_build_invite(server_port, client_port, rtp_port))

        # 6. Expect 100 Trying + 200 OK
        responses = await _recv_responses(sip_proto.queue, 2, timeout=5.0)
        trying = parse_message(responses[0])
        assert trying.uri == "100"
        ok = parse_message(responses[1])
        assert ok.uri == "200"
        assert ok.body and "m=audio" in ok.body

        # 7. ACK → triggers bridge setup
        sip_client_transport.sendto(_build_ack(server_port, client_port))

        # 8. Wait for RTP packets (~4s for 200 ticks at 20ms pacing)
        for _ in range(2500):
            await asyncio.sleep(0.01)
            if len(collector.packets) >= TICKS:
                break

        assert len(collector.packets) >= TICKS, (
            f"Only received {len(collector.packets)}/{TICKS} RTP packets"
        )

        # 9. Verify audio content via golden file
        received_ulaw = b""
        for pkt in collector.packets[:TICKS]:
            received_ulaw += parse_rtp_payload(pkt)

        pcm_8k = ulaw_to_pcm(received_ulaw)
        wav_bytes = pcm_to_wav(pcm_8k, channels=1, sampwidth=2, framerate=8000)

        check_fn = partial(wav_samples_check, max_rmse=30.0, min_correlation=0.999)
        file_regression.check(
            wav_bytes, binary=True, extension=".wav", check_fn=check_fn
        )

        # 10. Verify FFT peaks at chord frequencies
        pcm_arr = np.frombuffer(pcm_8k, dtype=np.int16).astype(np.float64)
        fft_mag = np.abs(np.fft.rfft(pcm_arr))
        freqs = np.fft.rfftfreq(len(pcm_arr), d=1.0 / SAMPLE_RATE_8K)

        for tone_freq in CHORD_FREQS:
            if tone_freq >= SAMPLE_RATE_8K / 2:
                continue  # Above Nyquist for 8kHz
            idx = np.argmin(np.abs(freqs - tone_freq))
            # Check that there's a peak near this frequency (within 5 bins)
            local_max = np.max(fft_mag[max(0, idx - 5) : idx + 6])
            assert local_max > fft_mag.mean() * 5, (
                f"No FFT peak near {tone_freq}Hz (local_max={local_max:.0f}, "
                f"mean={fft_mag.mean():.0f})"
            )

        # 11. BYE
        sip_client_transport.sendto(_build_bye(server_port, client_port))
        bye_resp = parse_message(await _recv(sip_proto.queue))
        assert bye_resp.uri == "200"

    finally:
        sip_client_transport.close()
        sip_transport.close()
        rtp_recv_transport.close()
