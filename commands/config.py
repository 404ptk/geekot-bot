import discord
from discord import app_commands

from commands import relations


async def setup_config_commands(
    client: discord.Client,
    tree: app_commands.CommandTree,
    guild_id: int = None,
):
    """Register the shared entry point for bot configuration commands."""
    guild = discord.Object(id=guild_id if guild_id else relations.GUILD_ID)
    config_group = app_commands.Group(
        name="config", description="Konfiguracja bota"
    )
    relations_group = app_commands.Group(
        name="relacje", description="Zarządzanie użytkownikami relacji"
    )
    relations.register_relations_config_commands(relations_group)
    config_group.add_command(relations_group)
    tree.add_command(config_group, guild=guild)
