# tests/test_voice_rx_decoder.py
"""Tests for discord_voice_rx decoder: jitter buffer and decoder thread."""

from unittest.mock import MagicMock

import numpy as np

from frizzle_phone.audio_utils import stereo_to_mono
from frizzle_phone.discord_voice_rx.decoder import (
    DecoderThread,
    JitterBuffer,
)
from frizzle_phone.discord_voice_rx.stats import VoiceRecvStats


class TestJitterBuffer:
    def _make_packet(self, ssrc: int = 1, seq: int = 0, ts: int = 0):
        pkt = MagicMock()
        pkt.ssrc = ssrc
        pkt.sequence = seq
        pkt.timestamp = ts
        # Make it sortable for the heap
        pkt.__lt__ = lambda s, o: s.sequence < o.sequence
        pkt.__gt__ = lambda s, o: s.sequence > o.sequence
        pkt.__eq__ = lambda s, o: s.sequence == o.sequence
        pkt.__bool__ = lambda s: True  # real packet, not fake
        return pkt

    def test_push_and_pop_ordered(self):
        buf = JitterBuffer(prefill=1)
        p1 = self._make_packet(seq=1)
        p2 = self._make_packet(seq=2)
        buf.push(p1)
        buf.push(p2)
        assert buf.pop() is p1
        assert buf.pop() is p2

    def test_reorders_out_of_order(self):
        buf = JitterBuffer(prefill=2)
        p1 = self._make_packet(seq=1)
        p2 = self._make_packet(seq=2)
        p3 = self._make_packet(seq=3)
        buf.push(p3)
        buf.push(p1)
        buf.push(p2)
        pkt = buf.pop()
        assert pkt is not None and pkt.sequence == 1
        pkt = buf.pop()
        assert pkt is not None and pkt.sequence == 2
        pkt = buf.pop()
        assert pkt is not None and pkt.sequence == 3

    def test_pop_empty_returns_none(self):
        buf = JitterBuffer(prefill=1)
        assert buf.pop() is None

    def test_prefill_delays_output(self):
        buf = JitterBuffer(prefill=2)
        p1 = self._make_packet(seq=1)
        buf.push(p1)
        assert buf.pop() is None  # prefill not met yet
        p2 = self._make_packet(seq=2)
        buf.push(p2)
        assert buf.pop() is not None  # prefill met

    def test_overflow_drops_oldest(self):
        buf = JitterBuffer(prefill=0, maxsize=3)
        for i in range(5):
            buf.push(self._make_packet(seq=i))
        # Only 3 should remain (newest)
        assert len(buf) <= 3

    def test_reset_clears(self):
        buf = JitterBuffer(prefill=1)
        buf.push(self._make_packet(seq=1))
        buf.push(self._make_packet(seq=2))
        buf.reset()
        assert buf.pop() is None
        assert len(buf) == 0

    def test_gap_detection(self):
        """After popping seq=1, with seq=3 buffered, gap should be 1."""
        buf = JitterBuffer(prefill=0)
        buf.push(self._make_packet(seq=1))
        buf.push(self._make_packet(seq=3))
        buf.pop()  # pops seq=1
        assert buf.gap() >= 1


class TestDecoderThread:
    def test_pop_tick_empty_when_no_data(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        result = dt.pop_tick()
        assert result == {}

    def test_pop_tick_returns_one_frame_per_user(self):
        """Feed two users' decoded frames, pop_tick returns one per user."""
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        dt.start()
        try:
            # Manually push decoded frames into per-user buffers (bypassing opus)
            mono = np.zeros(960, dtype=np.int16)
            with dt._lock:
                dt._user_buffers[1].append(mono.copy())
                dt._user_buffers[1].append(mono.copy())  # 2 frames for user 1
                dt._user_buffers[2].append(mono.copy())  # 1 frame for user 2

            tick = dt.pop_tick()
            assert set(tick.keys()) == {1, 2}
            assert len(tick[1]) == 960
            assert len(tick[2]) == 960

            # User 1 had 2 frames, so one should remain
            tick2 = dt.pop_tick()
            assert 1 in tick2
            assert 2 not in tick2
        finally:
            dt.stop()

    def test_pop_tick_increments_stats(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        dt.pop_tick()
        assert stats.ticks_empty == 1

    def test_stop_terminates_thread(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        dt.start()
        dt.stop()
        dt.join(timeout=2.0)
        assert not dt.is_alive()

    def test_destroy_decoder_clears_user_buffer(self):
        stats = VoiceRecvStats()
        dt = DecoderThread(stats=stats)
        mono = np.zeros(960, dtype=np.int16)
        with dt._lock:
            dt._user_buffers[42].append(mono)
        dt.destroy_decoder(ssrc=1234, user_id=42)
        tick = dt.pop_tick()
        assert 42 not in tick


class TestStereoToMono:
    def test_halves_length(self):
        stereo = b"\x00" * 3840  # 960 stereo samples
        mono = stereo_to_mono(stereo)
        assert isinstance(mono, np.ndarray)
        assert len(mono) == 960

    def test_averages_channels(self):
        stereo = np.array([100, 200], dtype=np.int16).tobytes()
        mono = stereo_to_mono(stereo)
        assert mono[0] == 150
