"""Vulture whitelist — attributes accessed via Jinja2 templates."""

# Used in extensions.html template (guild.id, channel.id)
_.id  # type: ignore[name-defined]
