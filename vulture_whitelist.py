"""Vulture whitelist — attributes used dynamically."""

# Used in extensions.html template (guild.id, channel.id)
_.id  # type: ignore[name-defined]

# unittest.mock return_value/side_effect assignment (vulture can't trace mock attribute usage)
_.return_value  # type: ignore[name-defined]
_.side_effect  # type: ignore[name-defined]

# __class__ assignment used in tests to control isinstance() behavior on mocks
_.__class__  # type: ignore[name-defined]

# discord_voice_rx: decrypt modes resolved via getattr(self, "_decrypt_rtp_" + mode)
_._decrypt_rtp_xsalsa20_poly1305  # type: ignore[name-defined]
_._decrypt_rtp_xsalsa20_poly1305_suffix  # type: ignore[name-defined]
_._decrypt_rtp_xsalsa20_poly1305_lite  # type: ignore[name-defined]
_._decrypt_rtp_aead_xchacha20_poly1305_rtpsize  # type: ignore[name-defined]
_.supported_modes  # type: ignore[name-defined]

# discord_voice_rx: discord.py overrides and future integration points
_.create_connection_state  # type: ignore[name-defined]
_.start_listening  # type: ignore[name-defined]
_.recv_stats  # type: ignore[name-defined]
_.flush  # type: ignore[name-defined]
_.padding  # type: ignore[name-defined]
_.csrcs  # type: ignore[name-defined]

# discord_voice_rx: FakePacket used by gap-filling logic (not yet wired)
FakePacket  # type: ignore[name-defined]

# discord_voice_rx: mock comparison dunders used by heapq in tests
_.__lt__  # type: ignore[name-defined]
_.__gt__  # type: ignore[name-defined]
_.__eq__  # type: ignore[name-defined]
_.__bool__  # type: ignore[name-defined]
