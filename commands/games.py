"""Shared game catalogue managed through a Components V2 panel."""
import json
import logging
import os
import uuid
from pathlib import Path

import discord
from discord import app_commands
from startup_logger import record_startup_step

GAMES_FILE = "txt/gry.json"
GUILD_ID = 551503797067710504
PAGE_SIZE = 5
logger = logging.getLogger(__name__)
MENTIONS = discord.AllowedMentions.none()


def save_games(games):
    path = Path(GAMES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(games, indent=4, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_games(startup_label=None):
    if not os.path.exists(GAMES_FILE):
        if startup_label:
            record_startup_step(startup_label, False, f"{GAMES_FILE} not found")
        return []
    with open(GAMES_FILE, encoding="utf-8") as stream:
        games = json.load(stream)
    # Persist identifiers once, preserving names, descriptions and existing metadata.
    changed = False
    seen = set()
    for game in games:
        if not isinstance(game.get("id"), str) or not game["id"] or game["id"] in seen:
            game["id"] = uuid.uuid4().hex
            changed = True
        seen.add(game["id"])
    if changed:
        save_games(games)
    if startup_label:
        record_startup_step(startup_label, True, GAMES_FILE)
    return games


def escaped(text, limit):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(text)))[:limit]


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=MENTIONS)


class GameButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction):
        await self.view.action(interaction, self.action)


class GameSelect(discord.ui.Select):
    def __init__(self, games, selected):
        super().__init__(placeholder="Wybierz grę, aby zobaczyć opis i ją edytować", options=[
            discord.SelectOption(label=game["name"][:100], value=game["id"],
                                 default=game["id"] == selected)
            for game in games
        ])

    async def callback(self, interaction):
        self.view.selected_id = self.values[0]
        self.view.detail_page = 0
        await self.view.refresh(interaction)


class GameModal(discord.ui.Modal):
    def __init__(self, panel, game=None):
        super().__init__(title="Edytuj grę" if game else "Dodaj grę", timeout=600)
        self.panel = panel
        self.original = dict(game) if game else None
        self.name_input = discord.ui.TextInput(
            label="Nazwa gry", min_length=1, max_length=100,
            default=game["name"][:100] if game else None)
        self.description_input = discord.ui.TextInput(
            label="Opis (opcjonalny)", style=discord.TextStyle.paragraph,
            required=False, max_length=4000,
            default=game.get("description", "")[:4000] if game else None)
        self.add_item(self.name_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction):
        if interaction.user.id != self.panel.owner_id:
            await reply(interaction, "Otwórz własny panel przez /gry.")
            return
        if self.panel.is_finished():
            await reply(interaction, "Panel wygasł. Otwórz nowy przez /gry.")
            return
        name = self.name_input.value.strip()
        if not name:
            await reply(interaction, "Podaj nazwę gry.")
            return
        description = self.description_input.value.strip()
        # Keep legacy text beyond the modal limit when that field is unchanged.
        if self.original:
            old_name = self.original["name"]
            old_description = self.original.get("description", "")
            if len(old_name) > 100 and name == old_name[:100].strip():
                name = old_name
            if len(old_description) > 4000 and description == old_description[:4000].strip():
                description = old_description
        games = load_games()
        if self.original:
            game = next((item for item in games if item["id"] == self.original["id"]), None)
            if game != self.original:
                await reply(interaction, "Ta gra została zmieniona lub usunięta. Odśwież panel i otwórz edycję ponownie.")
                return
            game.update(name=name, description=description)
        else:
            game = {"id": uuid.uuid4().hex, "name": name,
                    "description": description}
            games.append(game)
        save_games(games)
        self.panel.selected_id = game["id"]
        self.panel.detail_page = 0
        self.panel.page = games.index(game) // PAGE_SIZE
        await self.panel.refresh(interaction)

    async def on_error(self, interaction, error):
        logger.error("Game form failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zapisać gry. Spróbuj ponownie.")


class GamesPanel(discord.ui.LayoutView):
    def __init__(self, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.page = 0
        self.selected_id = None
        self.detail_page = 0
        self.message = None
        self.render()

    def render(self):
        games = load_games()
        pages = max(1, (len(games) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        visible = games[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        selected = next((game for game in visible if game["id"] == self.selected_id), None)
        if selected is None:
            self.selected_id = None
        self.clear_items()
        container = discord.ui.Container(accent_color=0x5865F2)
        container.add_item(discord.ui.TextDisplay(
            f"# 🎮 Gry\nLista gier, w które chcemy zagrać. **Łącznie: {len(games)}**"))
        container.add_item(discord.ui.Separator())
        if not visible:
            container.add_item(discord.ui.TextDisplay("Brak gier na liście. Dodaj pierwszą przyciskiem poniżej."))
        for index, game in enumerate(visible, self.page * PAGE_SIZE + 1):
            description = game.get("description") or "Brak opisu"
            preview = escaped(description, 250)
            if len(description) > 250:
                preview += "…"
            text = f"### {index}. {escaped(game['name'], 100)}"
            if selected is None:
                text += f"\n{preview}"
            container.add_item(discord.ui.TextDisplay(text))
        if visible:
            container.add_item(discord.ui.ActionRow(GameSelect(visible, self.selected_id)))
        if selected:
            container.add_item(discord.ui.Separator())
            description = selected.get("description") or "Brak opisu"
            detail_text = discord.utils.escape_markdown(discord.utils.escape_mentions(description))
            detail_pages = [detail_text[i:i + 1800] for i in range(0, len(detail_text), 1800)]
            self.detail_page = max(0, min(self.detail_page, len(detail_pages) - 1))
            detail = detail_pages[self.detail_page]
            container.add_item(discord.ui.TextDisplay(
                f"### Wybrano: {escaped(selected['name'], 100)}\n{detail}"))
            if len(detail_pages) > 1:
                container.add_item(discord.ui.TextDisplay(
                    f"-# Opis: strona {self.detail_page + 1} z {len(detail_pages)}"))
                container.add_item(discord.ui.ActionRow(
                    GameButton("detail_previous", "Poprzednia część opisu", disabled=self.detail_page == 0),
                    GameButton("detail_next", "Następna część opisu",
                               disabled=self.detail_page == len(detail_pages) - 1)))
        container.add_item(discord.ui.ActionRow(
            GameButton("add", "Dodaj grę", discord.ButtonStyle.success),
            GameButton("edit", "Edytuj nazwę / opis", discord.ButtonStyle.primary, selected is None),
            GameButton("delete", "Usuń", discord.ButtonStyle.danger, selected is None),
            GameButton("refresh", "Odśwież")))
        container.add_item(discord.ui.TextDisplay(f"-# Strona {self.page + 1} z {pages}"))
        container.add_item(discord.ui.ActionRow(
            GameButton("previous", "Poprzednia", disabled=self.page == 0),
            GameButton("next", "Następna", disabled=self.page == pages - 1)))
        self.footer = discord.ui.TextDisplay(
            "-# Panel wygasa po 10 minutach bezczynności.")
        container.add_item(self.footer)
        self.add_item(container)

    async def refresh(self, interaction):
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=MENTIONS)

    async def action(self, interaction, action):
        games = load_games()
        selected = next((game for game in games if game["id"] == self.selected_id), None)
        if action == "add":
            await interaction.response.send_modal(GameModal(self))
            return
        if action in ("edit", "delete") and selected is None:
            await reply(interaction, "Gra została usunięta. Odśwież panel.")
            return
        if action == "edit":
            await interaction.response.send_modal(GameModal(self, selected))
            return
        if action == "delete":
            games.remove(selected)
            save_games(games)
            self.selected_id = None
        elif action == "previous":
            self.page -= 1
            self.selected_id = None
        elif action == "next":
            self.page += 1
            self.selected_id = None
        elif action == "detail_previous":
            self.detail_page -= 1
        elif action == "detail_next":
            self.detail_page += 1
        await self.refresh(interaction)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await reply(interaction, "Otwórz własny panel przez /gry.")
        return False

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Select, discord.ui.Button)):
                item.disabled = True
        self.footer.content = "-# Panel wygasł. Otwórz nowy przez /gry."
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error("Games panel failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zaktualizować panelu. Spróbuj ponownie.")


async def setup_games_commands(client: discord.Client, tree: app_commands.CommandTree):
    @tree.command(name="gry", description="Panel listy gier: przeglądaj, dodawaj, edytuj i usuwaj",
                  guild=discord.Object(id=GUILD_ID))
    async def gry(interaction: discord.Interaction):
        panel = GamesPanel(interaction.user.id)
        await interaction.response.send_message(view=panel, allowed_mentions=MENTIONS)
        panel.message = await interaction.original_response()
