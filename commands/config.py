"""Shared configuration panel; feature screens register through CONFIG_SECTIONS."""
from dataclasses import dataclass
from typing import Callable

import discord
from discord import app_commands

from commands import relations


@dataclass(frozen=True)
class ConfigSection:
    key: str
    category: str
    title: str
    description: str
    open_view: Callable


class ConfigView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Otwórz własny panel przez /config.", ephemeral=True)
            return False
        return True

    async def show(self, interaction: discord.Interaction, view, embed):
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = interaction.message or self.message
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Panel wygasł. Wpisz /config, aby otworzyć go ponownie.", view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        print(f"Config panel error: {error}")
        message = "Nie udało się wykonać operacji. Spróbuj ponownie."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class SectionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="Wybierz ustawienia…", options=[
            discord.SelectOption(label=f"{section.category} · {section.title}", value=section.key,
                                 description=section.description)
            for section in CONFIG_SECTIONS
        ])

    async def callback(self, interaction):
        section = next(section for section in CONFIG_SECTIONS if section.key == self.values[0])
        view = section.open_view(self.view.owner_id)
        await self.view.show(interaction, view, view.embed())


class ConfigHomeView(ConfigView):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.add_item(SectionSelect())

    def embed(self):
        embed = discord.Embed(title="⚙️ Konfiguracja bota", description="Wybierz ustawienia z listy poniżej.", color=discord.Color.blurple())
        categories = dict.fromkeys(section.category for section in CONFIG_SECTIONS)
        for category in categories:
            embed.add_field(name=category, value="\n".join(
                f"**{section.title}** — {section.description}"
                for section in CONFIG_SECTIONS if section.category == category
            ), inline=False)
        embed.set_footer(text="Panel widoczny tylko dla Ciebie • wygasa po 10 minutach bezczynności")
        return embed


class RelationsView(ConfigView):
    def embed(self, notice=None):
        users = relations.load_relation_users()
        names = ", ".join(discord.utils.escape_markdown(nick) for nick in users) or "Lista jest pusta."
        embed = discord.Embed(title="⚙️ Społeczność → Relacje", color=discord.Color.blurple(),
                              description="Zarządzaj użytkownikami dostępnymi w relacjach.")
        embed.add_field(name=f"Użytkownicy ({len(users)})", value=names[:1020], inline=False)
        if notice:
            embed.add_field(name="Wynik", value=notice, inline=False)
        return embed

    @discord.ui.button(label="Dodaj użytkownika", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await interaction.response.send_modal(AddRelationUserModal(self))

    @discord.ui.button(label="Usuń użytkownika", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        view = RemoveRelationUserView(self.owner_id)
        await self.show(interaction, view, view.embed())

    @discord.ui.button(label="Menu główne", row=1)
    async def home(self, interaction, button):
        view = ConfigHomeView(self.owner_id)
        await self.show(interaction, view, view.embed())


class AddRelationUserModal(discord.ui.Modal, title="Dodaj użytkownika do relacji"):
    nick = discord.ui.Label(text="Nick", description="Np. jaro: „jaro trzyma zgodę z kuzią”",
                            component=discord.ui.TextInput(min_length=1, max_length=100))
    celownik = discord.ui.Label(text="Celownik — komu?", description="Np. jarowi: „kuzia wypowiedział kosę jarowi”",
                                component=discord.ui.TextInput(min_length=1, max_length=100))
    narzednik = discord.ui.Label(text="Narzędnik — z kim?", description="Np. jarem: „kuzia trzyma zgodę z jarem”",
                                 component=discord.ui.TextInput(min_length=1, max_length=100))

    def __init__(self, panel):
        super().__init__(timeout=600)
        self.panel = panel

    async def interaction_check(self, interaction):
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction):
        try:
            nick = relations.add_relation_user(self.nick.component.value, self.celownik.component.value,
                                               self.narzednik.component.value)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        view = RelationsView(self.panel.owner_id)
        await self.panel.show(interaction, view, view.embed(f"Dodano **{discord.utils.escape_markdown(nick)}**."))

    async def on_error(self, interaction, error):
        await self.panel.on_error(interaction, error, None)


class UserSelect(discord.ui.Select):
    def __init__(self, users):
        super().__init__(placeholder="Wybierz użytkownika do usunięcia…", options=[
            discord.SelectOption(label=nick, value=nick) for nick in users
        ])

    async def callback(self, interaction):
        view = RemoveRelationUserView(self.view.owner_id, self.view.page, self.values[0])
        await self.view.show(interaction, view, view.embed())


class RemoveRelationUserView(ConfigView):
    def __init__(self, owner_id, page=0, selected=None):
        super().__init__(owner_id)
        users = sorted(relations.load_relation_users())
        self.pages = max(1, (len(users) + 24) // 25)
        self.page = min(max(page, 0), self.pages - 1)
        self.selected = selected if selected in users else None
        if users:
            self.add_item(UserSelect(users[self.page * 25:(self.page + 1) * 25]))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == self.pages - 1
        self.confirm.disabled = self.selected is None

    def embed(self):
        text = "Wybierz nick z listy, a następnie kliknij „Usuń”. Usunięte zostaną też wszystkie relacje i tymczasowe zgody tej osoby."
        if not relations.load_relation_users():
            text = "Lista użytkowników jest pusta."
        if self.selected:
            text += f"\n\nWybrano: **{discord.utils.escape_markdown(self.selected)}**"
        embed = discord.Embed(title="⚙️ Relacje → Usuń użytkownika", description=text, color=discord.Color.orange())
        embed.set_footer(text=f"Strona {self.page + 1}/{self.pages}")
        return embed

    @discord.ui.button(label="Poprzednia strona", row=1)
    async def previous(self, interaction, button):
        view = RemoveRelationUserView(self.owner_id, self.page - 1)
        await self.show(interaction, view, view.embed())

    @discord.ui.button(label="Następna strona", row=1)
    async def next_page(self, interaction, button):
        view = RemoveRelationUserView(self.owner_id, self.page + 1)
        await self.show(interaction, view, view.embed())

    @discord.ui.button(label="Usuń", style=discord.ButtonStyle.danger, row=2)
    async def confirm(self, interaction, button):
        try:
            relations.remove_relation_user(self.selected)
            notice = f"Usunięto **{discord.utils.escape_markdown(self.selected)}** i jego relacje."
        except ValueError as error:
            notice = str(error)
        view = RelationsView(self.owner_id)
        await self.show(interaction, view, view.embed(notice))

    @discord.ui.button(label="Wróć do relacji", row=2)
    async def back(self, interaction, button):
        view = RelationsView(self.owner_id)
        await self.show(interaction, view, view.embed())


# Add future screens here; configuration navigation stays in this module.
CONFIG_SECTIONS = (
    ConfigSection("relations", "Społeczność", "Relacje", "Użytkownicy i odmiana nicków", RelationsView),
)


async def setup_config_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id: int = None):
    guild = discord.Object(id=guild_id if guild_id else relations.GUILD_ID)

    @tree.command(name="config", description="Otwiera prywatny panel konfiguracji bota", guild=guild)
    async def config(interaction: discord.Interaction):
        view = ConfigHomeView(interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()
