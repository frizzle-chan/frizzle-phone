"""Vulture whitelist — attributes used dynamically."""

# Used in extensions.html template (guild.id, channel.id)
_.id  # type: ignore[name-defined]

# Monkey-patched onto PacketRouter (voice_recv opus error workaround)
_._do_run  # type: ignore[name-defined]

# unittest.mock return_value assignment (vulture can't trace mock attribute usage)
_.return_value  # type: ignore[name-defined]
