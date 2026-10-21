"""Masny's excuses managed from a single Components V2 panel."""
import logging
import random
from pathlib import Path

import discord
from discord import app_commands

WYMOWKI_FILE = "txt/wymowki.txt"
GUILD_ID = 551503797067710504
PAGE_SIZE = 8
MAX_EXCUSE_LENGTH = 500
MENTIONS = discord.AllowedMentions.none()
logger = logging.getLogger(__name__)


def load_wymowki():
    path = Path(WYMOWKI_FILE)
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_wymowki(wymowki):
    path = Path(WYMOWKI_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(wymowki), encoding="utf-8")
    temporary.replace(path)


def escaped(text):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=MENTIONS)


class ExcuseButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction):
        await self.view.action(interaction, self.action)


class ExcuseSelect(discord.ui.Select):
    def __init__(self, panel):
        start = panel.page * PAGE_SIZE
        options = [discord.SelectOption(
            label=f"{index + 1}. {text}"[:100], value=str(index),
            default=panel.selected == index,
        ) for index, text in enumerate(panel.entries[start:start + PAGE_SIZE], start)]
        super().__init__(placeholder="Wybierz wymówkę, aby zobaczyć całość lub ją usunąć", options=options)
        self.snapshot = panel.entries.copy()

    async def callback(self, interaction):
        panel = self.view
        if load_wymowki() != self.snapshot:
            panel.selected = None
            panel.notice = "Lista się zmieniła. Wybierz wymówkę ponownie."
        else:
            panel.selected = int(self.values[0])
            panel.selection_snapshot = self.snapshot.copy()
            panel.detail_page = 0
            panel.notice = ""
        await panel.refresh(interaction)


class AddExcuseModal(discord.ui.Modal, title="Dodaj wymówkę Masnego"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.text = discord.ui.TextInput(
            label="Treść wymówki", style=discord.TextStyle.paragraph,
            min_length=1, max_length=MAX_EXCUSE_LENGTH,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction):
        if not await self.panel.interaction_check(interaction):
            return
        text = " ".join(self.text.value.split())
        if not text or len(text) > MAX_EXCUSE_LENGTH:
            await reply(interaction, "Podaj treść wymówki (od 1 do 500 znaków).")
            return
        entries = load_wymowki()
        entries.append(text)
        save_wymowki(entries)
        self.panel.selected = len(entries) - 1
        self.panel.selection_snapshot = entries.copy()
        self.panel.page = self.panel.selected // PAGE_SIZE
        self.panel.detail_page = 0
        self.panel.show_help = False
        self.panel.notice = "✅ Dodano wymówkę."
        await self.panel.refresh(interaction)

    async def on_error(self, interaction, error):
        logger.error("Excuse form failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zapisać wymówki. Spróbuj ponownie.")


class ExcusesPanel(discord.ui.LayoutView):
    def __init__(self, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.message = None
        self.page = 0
        self.selected = None
        self.selection_snapshot = None
        self.detail_page = 0
        self.show_help = False
        self.notice = ""
        self.render()

    def render(self):
        self.entries = load_wymowki()
        if self.selection_snapshot != self.entries:
            self.selected = None
        pages = max(1, (len(self.entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        self.clear_items()
        container = discord.ui.Container(accent_color=0x9B59B6)
        container.add_item(discord.ui.TextDisplay(
            f"## 🎭 Wymówki Masnego\n-# Zapisanych wymówek: {len(self.entries)}"))
        if self.notice:
            container.add_item(discord.ui.TextDisplay(self.notice))
        container.add_item(discord.ui.Separator())
        if self.show_help:
            container.add_item(discord.ui.TextDisplay(
                "### Jak korzystać z panelu\n"
                "**Losuj** — wybierz losową wymówkę.\n"
                "**Dodaj** — wpisz nową wymówkę w formularzu (maks. 500 znaków).\n"
                "**Lista** — przeglądaj wymówki i wybierz jedną z menu, aby zobaczyć całość.\n"
                "**Usuń** — usuń wybraną wymówkę.\n"
                "**Odśwież** — pobierz aktualną listę."))
        else:
            start = self.page * PAGE_SIZE
            visible = self.entries[start:start + PAGE_SIZE]
            if not visible:
                container.add_item(discord.ui.TextDisplay("Brak wymówek. Dodaj pierwszą przyciskiem poniżej."))
            for index, text in enumerate(visible, start):
                preview = escaped(text)
                if len(preview) > 160:
                    preview = preview[:157] + "…"
                container.add_item(discord.ui.TextDisplay(f"**{index + 1}.** {preview}"))
            if visible:
                container.add_item(discord.ui.ActionRow(ExcuseSelect(self)))
            if self.selected is not None:
                container.add_item(discord.ui.Separator())
                text = escaped(self.entries[self.selected])
                chunks = [text[i:i + 1200] for i in range(0, len(text), 1200)]
                self.detail_page = max(0, min(self.detail_page, len(chunks) - 1))
                container.add_item(discord.ui.TextDisplay(
                    f"### Wymówka #{self.selected + 1}\n{chunks[self.detail_page]}"))
                if len(chunks) > 1:
                    container.add_item(discord.ui.TextDisplay(
                        f"-# Treść: część {self.detail_page + 1} z {len(chunks)}"))
                    container.add_item(discord.ui.ActionRow(
                        ExcuseButton("detail_previous", "Poprzednia część", disabled=self.detail_page == 0),
                        ExcuseButton("detail_next", "Następna część", disabled=self.detail_page == len(chunks) - 1)))
            container.add_item(discord.ui.TextDisplay(f"-# Strona {self.page + 1} z {pages}"))
            container.add_item(discord.ui.ActionRow(
                ExcuseButton("previous", "Poprzednia", disabled=self.page == 0),
                ExcuseButton("next", "Następna", disabled=self.page == pages - 1)))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(
            ExcuseButton("random", "Losuj", discord.ButtonStyle.primary, not self.entries),
            ExcuseButton("add", "Dodaj", discord.ButtonStyle.success),
            ExcuseButton("list", "Lista"),
            ExcuseButton("delete", "Usuń", discord.ButtonStyle.danger,
                         self.selected is None or self.show_help),
            ExcuseButton("help", "Pomoc")))
        container.add_item(discord.ui.ActionRow(ExcuseButton("refresh", "Odśwież")))
        self.footer = discord.ui.TextDisplay("-# Panel wygasa po 10 minutach bezczynności.")
        container.add_item(self.footer)
        self.add_item(container)

    async def refresh(self, interaction):
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=MENTIONS)

    async def action(self, interaction, action):
        if action == "add":
            await interaction.response.send_modal(AddExcuseModal(self))
            return
        self.notice = ""
        entries = load_wymowki()
        if action == "delete":
            if self.selected is None or entries != self.selection_snapshot:
                self.selected = None
                self.notice = "Lista się zmieniła. Wybierz wymówkę ponownie."
            else:
                entries.pop(self.selected)
                save_wymowki(entries)
                self.selected = None
                self.notice = "🗑️ Usunięto wymówkę."
        else:
            self.show_help = action == "help"
            if action == "random" and entries:
                self.selected = random.randrange(len(entries))
                self.selection_snapshot = entries.copy()
                self.page = self.selected // PAGE_SIZE
                self.detail_page = 0
                self.notice = "🎲 Wylosowana wymówka:"
            elif action in ("previous", "next"):
                self.page += 1 if action == "next" else -1
                self.selected = None
                self.detail_page = 0
            elif action in ("detail_previous", "detail_next"):
                self.detail_page += 1 if action == "detail_next" else -1
        await self.refresh(interaction)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await reply(interaction, "Otwórz własny panel przez /wymowki.")
            return False
        if self.is_finished():
            await reply(interaction, "Panel wygasł. Otwórz nowy przez /wymowki.")
            return False
        return True

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        self.footer.content = "-# Panel wygasł. Otwórz nowy przez /wymowki."
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error("Excuse panel failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zaktualizować panelu. Spróbuj ponownie.")


async def setup_excuses_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id: int = None):
    guild = discord.Object(id=guild_id or GUILD_ID)

    @tree.command(name="wymowki", description="Panel wymówek Masnego: lista, losowanie, dodawanie i usuwanie", guild=guild)
    async def wymowki(interaction: discord.Interaction):
        panel = ExcusesPanel(interaction.user.id)
        await interaction.response.send_message(view=panel, allowed_mentions=MENTIONS)
        panel.message = await interaction.original_response()
