"""Discord bot factory — provides guild/channel enumeration for the webapp."""

import discord
from discord.ext import commands


def create_bot() -> commands.Bot:
    """Create a Bot with guilds + voice_states intents (no commands)."""
    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True
    return commands.Bot(command_prefix="!", intents=intents)
