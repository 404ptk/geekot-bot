"""Shared configuration panel; feature screens register through CONFIG_SECTIONS."""
from dataclasses import dataclass
from typing import Callable

import discord
from discord import app_commands

from commands import relations
from config.conf_panel import ConfigView, require_administrator
from config.conf_faceit import FaceitView
from config.conf_relations import RelationsView
from config.conf_steam import SteamView
from config.conf_permissions import PermissionsView


@dataclass(frozen=True)
class ConfigSection:
    key: str
    title: str
    description: str
    open_view: Callable
    guild_scoped: bool = False


class SectionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Wybierz ustawienia…", options=[
            discord.SelectOption(label=section.title, value=section.key,
                                 description=section.description)
            for section in CONFIG_SECTIONS
        ])

    async def callback(self, interaction):
        section = next(section for section in CONFIG_SECTIONS if section.key == self.values[0])
        view = (section.open_view(self.view.owner_id, interaction.guild) if section.guild_scoped
                else section.open_view(self.view.owner_id))
        await self.view.show(interaction, view, view.embed())


class ConfigHomeView(ConfigView):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.add_item(SectionSelect())

    def embed(self):
        embed = discord.Embed(title="⚙️ Konfiguracja bota", description="Wybierz ustawienia z listy poniżej.", color=discord.Color.blurple())
        for section in CONFIG_SECTIONS:
            embed.add_field(name=section.title, value=section.description, inline=False)
        embed.set_footer(text="Panel widoczny tylko dla Ciebie • wygasa po 10 minutach bezczynności")
        return embed


# Add future screens here; configuration navigation stays in this module.
CONFIG_SECTIONS = (
    ConfigSection("relations", "Relacje", "Użytkownicy i odmiana nicków", RelationsView),
    ConfigSection("faceit", "Faceit", "Ustawienia funkcji Faceit", FaceitView),
    ConfigSection("steam", "Steam", "Kanał i źródła aktualizacji CS2", SteamView),
    ConfigSection("permissions", "Permisje", "Globalna ranga moderatora i dostęp do komend", PermissionsView, True),
)


async def setup_config_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id: int = None):
    guild = discord.Object(id=guild_id if guild_id else relations.GUILD_ID)

    @tree.command(name="config", description="Otwiera prywatny panel konfiguracji bota", guild=guild)
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def config(interaction: discord.Interaction):
        if not await require_administrator(interaction):
            return
        view = ConfigHomeView(interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()
