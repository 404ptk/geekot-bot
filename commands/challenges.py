"""Shared CS2 challenges managed through a Components V2 panel."""
import json
import logging
import random
import uuid
from pathlib import Path

import discord
from discord import app_commands
from startup_logger import record_startup_step

GUILD_ID = 551503797067710504
CHALLENGES_FILE = "txt/challenges.txt"
PAGE_SIZE = 5
MENTIONS = discord.AllowedMentions.none()
logger = logging.getLogger(__name__)
DEFAULT_CHALLENGES = [
    "Zagraj rundę tylko z Deagle",
    "Wygraj mecz bez kupowania granatów",
    "Użyj tylko noża w jednej rundzie",
    "Zabij 3 przeciwników z AWP w jednym meczu",
]


def save_challenges(records):
    path = Path(CHALLENGES_FILE).with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=4), encoding="utf-8")
    temporary.replace(path)


def load_challenges(startup_label=None):
    legacy_path = Path(CHALLENGES_FILE)
    path = legacy_path.with_suffix(".json")
    if path.exists():
        records = json.loads(path.read_text(encoding="utf-8"))
    else:
        texts = legacy_path.read_text(encoding="utf-8").splitlines() if legacy_path.exists() else DEFAULT_CHALLENGES
        records = [{"id": uuid.uuid4().hex, "text": text.strip()} for text in texts if text.strip()]
        # Keep the original text file as a backup; subsequent edits use JSON.
        save_challenges(records)
    if startup_label:
        record_startup_step(startup_label, True, str(path))
    return records


def escaped(value):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=MENTIONS)


class ChallengeButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction):
        await self.view.action(interaction, self.action)


class ChallengeSelect(discord.ui.Select):
    def __init__(self, records, selected_id):
        super().__init__(placeholder="Wybierz wyzwanie, aby zobaczyć całą treść", options=[
            discord.SelectOption(label=record["text"].replace("\n", " ")[:100], value=record["id"],
                                 default=record["id"] == selected_id)
            for record in records])

    async def callback(self, interaction):
        self.view.selected_id = self.values[0]
        self.view.detail_page = 0
        self.view.drawn = False
        await self.view.refresh(interaction)


class ChallengeModal(discord.ui.Modal):
    def __init__(self, panel, record=None):
        super().__init__(title="Edytuj wyzwanie" if record else "Dodaj wyzwanie", timeout=600)
        self.panel = panel
        self.original = dict(record) if record else None
        self.text_input = discord.ui.TextInput(
            label="Treść wyzwania", style=discord.TextStyle.paragraph, min_length=1, max_length=4000,
            default=record["text"][:4000] if record else None)
        self.add_item(self.text_input)

    async def on_submit(self, interaction):
        if interaction.user.id != self.panel.owner_id:
            await reply(interaction, "Otwórz własny panel przez /wyzwania.")
            return
        if self.panel.is_finished():
            await reply(interaction, "Panel wygasł. Otwórz nowy przez /wyzwania.")
            return
        text = self.text_input.value.strip()
        if not text:
            await reply(interaction, "Podaj treść wyzwania.")
            return
        if self.original and len(self.original["text"]) > 4000 and text == self.original["text"][:4000].strip():
            text = self.original["text"]
        records = load_challenges()
        if self.original:
            record = next((item for item in records if item["id"] == self.original["id"]), None)
            if record != self.original:
                await reply(interaction, "Wyzwanie zostało zmienione lub usunięte. Odśwież panel i otwórz edycję ponownie.")
                return
            record["text"] = text
        else:
            record = {"id": uuid.uuid4().hex, "text": text}
            records.append(record)
        save_challenges(records)
        self.panel.selected_id = record["id"]
        self.panel.page = records.index(record) // PAGE_SIZE
        self.panel.detail_page = 0
        self.panel.drawn = False
        await self.panel.refresh(interaction)

    async def on_error(self, interaction, error):
        logger.error("Challenge form failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zapisać wyzwania. Spróbuj ponownie.")


class ChallengesPanel(discord.ui.LayoutView):
    def __init__(self, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.page = 0
        self.detail_page = 0
        self.selected_id = None
        self.drawn = False
        self.message = None
        self.render()

    def render(self):
        records = load_challenges()
        pages = max(1, (len(records) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        visible = records[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        selected = next((item for item in visible if item["id"] == self.selected_id), None)
        if selected is None:
            self.selected_id = None
            self.drawn = False
        self.clear_items()
        container = discord.ui.Container(accent_color=0xE67E22)
        container.add_item(discord.ui.TextDisplay(
            f"# 🎯 Wyzwania CS2\nWspólna lista wyzwań. **Łącznie: {len(records)}**"))
        container.add_item(discord.ui.Separator())
        if not visible:
            container.add_item(discord.ui.TextDisplay("Brak wyzwań. Dodaj pierwsze przyciskiem poniżej."))
        for index, record in enumerate(visible, self.page * PAGE_SIZE + 1):
            preview = escaped(record["text"])
            preview = preview[:200] + "…" if len(preview) > 200 else preview
            container.add_item(discord.ui.TextDisplay(f"**{index}.** {preview}"))
        if visible:
            container.add_item(discord.ui.ActionRow(ChallengeSelect(visible, self.selected_id)))
        if selected:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                "### 🎲 Wylosowane wyzwanie" if self.drawn else "### Wybrane wyzwanie"))
            text = escaped(selected["text"])
            chunks = [text[i:i + 1800] for i in range(0, len(text), 1800)]
            self.detail_page = max(0, min(self.detail_page, len(chunks) - 1))
            container.add_item(discord.ui.TextDisplay(chunks[self.detail_page]))
            if len(chunks) > 1:
                container.add_item(discord.ui.TextDisplay(
                    f"-# Treść: strona {self.detail_page + 1} z {len(chunks)}"))
                container.add_item(discord.ui.ActionRow(
                    ChallengeButton("detail_previous", "Poprzednia część", disabled=self.detail_page == 0),
                    ChallengeButton("detail_next", "Następna część", disabled=self.detail_page == len(chunks) - 1)))
        container.add_item(discord.ui.ActionRow(
            ChallengeButton("draw", "Losuj", discord.ButtonStyle.primary, not records),
            ChallengeButton("add", "Dodaj", discord.ButtonStyle.success),
            ChallengeButton("edit", "Edytuj", disabled=selected is None),
            ChallengeButton("delete", "Usuń", discord.ButtonStyle.danger, selected is None),
            ChallengeButton("refresh", "Odśwież")))
        container.add_item(discord.ui.TextDisplay(f"-# Strona {self.page + 1} z {pages}"))
        container.add_item(discord.ui.ActionRow(
            ChallengeButton("previous", "Poprzednia", disabled=self.page == 0),
            ChallengeButton("next", "Następna", disabled=self.page == pages - 1)))
        self.footer = discord.ui.TextDisplay("-# Panel wygasa po 10 minutach bezczynności.")
        container.add_item(self.footer)
        self.add_item(container)

    async def refresh(self, interaction):
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=MENTIONS)

    async def action(self, interaction, action):
        records = load_challenges()
        selected = next((item for item in records if item["id"] == self.selected_id), None)
        if action == "add":
            await interaction.response.send_modal(ChallengeModal(self))
            return
        if action in ("edit", "delete") and selected is None:
            await reply(interaction, "Wyzwanie zostało usunięte. Odśwież panel.")
            return
        if action == "edit":
            await interaction.response.send_modal(ChallengeModal(self, selected))
            return
        if action == "delete":
            records.remove(selected)
            save_challenges(records)
            self.selected_id = None
        elif action == "draw":
            if not records:
                await reply(interaction, "Brak wyzwań do wylosowania.")
                return
            record = random.choice(records)
            self.selected_id = record["id"]
            self.page = records.index(record) // PAGE_SIZE
            self.detail_page = 0
            self.drawn = True
        elif action in ("previous", "next"):
            self.page += 1 if action == "next" else -1
            self.selected_id = None
            self.drawn = False
        elif action in ("detail_previous", "detail_next"):
            self.detail_page += 1 if action == "detail_next" else -1
        await self.refresh(interaction)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await reply(interaction, "Otwórz własny panel przez /wyzwania.")
        return False

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        self.footer.content = "-# Panel wygasł. Otwórz nowy przez /wyzwania."
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error("Challenges panel failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zaktualizować panelu. Spróbuj ponownie.")


async def setup_challenges_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id: int = None):
    guild = discord.Object(id=guild_id if guild_id is not None else GUILD_ID)
    load_challenges(startup_label="Challenges list")

    @tree.command(name="wyzwania", description="Panel wyzwań CS2: losuj, przeglądaj, dodawaj, edytuj i usuwaj", guild=guild)
    async def wyzwania(interaction: discord.Interaction):
        panel = ChallengesPanel(interaction.user.id)
        await interaction.response.send_message(view=panel, allowed_mentions=MENTIONS)
        panel.message = await interaction.original_response()
