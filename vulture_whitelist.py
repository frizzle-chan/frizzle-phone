"""Vulture whitelist — attributes used dynamically."""

# Used in extensions.html template (guild.id, channel.id)
_.id  # type: ignore[name-defined]

# Monkey-patched onto PacketRouter (voice_recv opus error workaround)
_._do_run  # type: ignore[name-defined]

# Monkey-patched onto AudioReader (DAVE decryption injection)
_.callback  # type: ignore[name-defined]

# Assigned on RTPPacket by patched callback (transport/DAVE decrypted payload)
_.decrypted_data  # type: ignore[name-defined]

# unittest.mock return_value/side_effect assignment (vulture can't trace mock attribute usage)
_.return_value  # type: ignore[name-defined]
_.side_effect  # type: ignore[name-defined]
