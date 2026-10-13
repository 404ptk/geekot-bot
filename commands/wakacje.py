"""Shared vacation proposals managed through Components V2."""
import json
import logging
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import discord
from discord import app_commands
from startup_logger import record_startup_step

WAKACJE_FILE = "txt/wakacje.json"
GUILD_ID = 551503797067710504
PAGE_SIZE = 4
MENTIONS = discord.AllowedMentions.none()
logger = logging.getLogger(__name__)


def save_wakacje(wakacje):
    path = Path(WAKACJE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(wakacje, indent=4, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_wakacje(startup_label=None):
    path = Path(WAKACJE_FILE)
    if not path.exists():
        if startup_label:
            record_startup_step(startup_label, False, f"{WAKACJE_FILE} not found")
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    changed = False
    for record in records:
        if not isinstance(record.get("id"), str) or not record["id"] or record["id"] in seen:
            record["id"] = str(uuid.uuid4())
            changed = True
        seen.add(record["id"])
    if changed:
        save_wakacje(records)
    if startup_label:
        record_startup_step(startup_label, True, WAKACJE_FILE)
    return records


def escaped(value, limit):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def valid_link(value):
    try:
        url = urlsplit(value)
        return len(value) <= 512 and url.scheme in ("https", "http") and bool(url.hostname) and not any(
            char.isspace() or ord(char) < 32 for char in value)
    except ValueError:
        return False


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=MENTIONS)


class VacationButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction):
        await self.view.action(interaction, self.action)


class VacationSelect(discord.ui.Select):
    def __init__(self, records, selected_id):
        super().__init__(placeholder="Wybierz propozycję, aby zobaczyć szczegóły", options=[
            discord.SelectOption(label=record["krotki_opis"][:100], value=record["id"],
                                 default=record["id"] == selected_id)
            for record in records])

    async def callback(self, interaction):
        self.view.selected_id = self.values[0]
        self.view.detail_page = 0
        await self.view.refresh(interaction)


class VacationModal(discord.ui.Modal):
    def __init__(self, panel, record=None, link_only=False):
        super().__init__(title="Link oferty" if link_only else "Edytuj propozycję" if record else "Dodaj propozycję",
                         timeout=600)
        self.panel = panel
        self.original = dict(record) if record else None
        self.inputs = {}
        specs = [("link", "Link oferty (puste pole usuwa link)", 512, False)] if link_only else [
            ("krotki_opis", "Nazwa propozycji", 30, True),
            ("kraj", "Kraj / miejsce", 100, True),
            ("data", "Termin", 100, True),
            ("kwota", "Kwota (np. 2000 zł / osoba)", 100, True),
            ("opis", "Opis (opcjonalny)", 4000, False)]
        for key, label, limit, required in specs:
            field = discord.ui.TextInput(
                label=label, max_length=limit, required=required,
                style=discord.TextStyle.paragraph if key == "opis" else discord.TextStyle.short,
                default=str(record.get(key, ""))[:limit] if record else None)
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction):
        if interaction.user.id != self.panel.owner_id:
            await reply(interaction, "Otwórz własny panel przez /wakacje.")
            return
        if self.panel.is_finished():
            await reply(interaction, "Panel wygasł. Otwórz nowy przez /wakacje.")
            return
        values = {key: field.value.strip() for key, field in self.inputs.items()}
        for key, field in self.inputs.items():
            if field.required and not values[key]:
                await reply(interaction, "Uzupełnij wymagane pola.")
                return
            if self.original:
                old = str(self.original.get(key, ""))
                if len(old) > field.max_length and values[key] == old[:field.max_length].strip():
                    values[key] = old
        if values.get("link") and not valid_link(values["link"]):
            await reply(interaction, "Podaj poprawny link HTTP lub HTTPS (maksymalnie 512 znaków).")
            return
        records = load_wakacje()
        if self.original:
            record = next((item for item in records if item["id"] == self.original["id"]), None)
            if record != self.original:
                await reply(interaction, "Propozycja została zmieniona lub usunięta. Odśwież panel i otwórz formularz ponownie.")
                return
        else:
            record = {"id": str(uuid.uuid4())}
        if "krotki_opis" in values and any(
                item["id"] != record["id"] and item["krotki_opis"].casefold() == values["krotki_opis"].casefold()
                for item in records):
            await reply(interaction, "Propozycja o tej nazwie już istnieje.")
            return
        record.update(values)
        if not self.original:
            records.append(record)
        save_wakacje(records)
        self.panel.selected_id = record["id"]
        self.panel.page = records.index(record) // PAGE_SIZE
        self.panel.detail_page = 0
        await self.panel.refresh(interaction)

    async def on_error(self, interaction, error):
        logger.error("Vacation form failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zapisać propozycji. Spróbuj ponownie.")


class VacationPanel(discord.ui.LayoutView):
    def __init__(self, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.page = 0
        self.detail_page = 0
        self.selected_id = None
        self.message = None
        self.render()

    def render(self):
        records = load_wakacje()
        pages = max(1, (len(records) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        visible = records[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        selected = next((item for item in visible if item["id"] == self.selected_id), None)
        if selected is None:
            self.selected_id = None
        self.clear_items()
        container = discord.ui.Container(accent_color=0xF39C12)
        container.add_item(discord.ui.TextDisplay(
            f"# 🏖️ Wakacje\nWspólne propozycje wyjazdów. **Łącznie: {len(records)}**"))
        container.add_item(discord.ui.Separator())
        if not visible:
            container.add_item(discord.ui.TextDisplay("Brak propozycji. Dodaj pierwszą przyciskiem poniżej."))
        for index, record in enumerate(visible, self.page * PAGE_SIZE + 1):
            text = f"### {index}. {escaped(record['krotki_opis'], 100)}"
            if selected is None:
                text += (f"\n🌍 {escaped(record.get('kraj', ''), 100)}"
                         f" · 📅 {escaped(record.get('data', ''), 100)}"
                         f" · 💰 {escaped(record.get('kwota', ''), 100)}")
            container.add_item(discord.ui.TextDisplay(text))
        if visible:
            container.add_item(discord.ui.ActionRow(VacationSelect(visible, self.selected_id)))
        if selected:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                f"### {escaped(selected['krotki_opis'], 100)}\n"
                f"🌍 **Miejsce:** {escaped(selected.get('kraj', ''), 100)}\n"
                f"📅 **Termin:** {escaped(selected.get('data', ''), 100)}\n"
                f"💰 **Kwota:** {escaped(selected.get('kwota', ''), 100)}"))
            description = discord.utils.escape_markdown(discord.utils.escape_mentions(
                selected.get("opis") or "Brak opisu"))
            chunks = [description[i:i + 1800] for i in range(0, len(description), 1800)]
            self.detail_page = max(0, min(self.detail_page, len(chunks) - 1))
            container.add_item(discord.ui.TextDisplay(chunks[self.detail_page]))
            if len(chunks) > 1:
                container.add_item(discord.ui.TextDisplay(
                    f"-# Opis: strona {self.detail_page + 1} z {len(chunks)}"))
                container.add_item(discord.ui.ActionRow(
                    VacationButton("detail_previous", "Poprzednia część opisu", disabled=self.detail_page == 0),
                    VacationButton("detail_next", "Następna część opisu", disabled=self.detail_page == len(chunks) - 1)))
            link = selected.get("link", "")
            if link and valid_link(link):
                container.add_item(discord.ui.ActionRow(
                    discord.ui.Button(label="Otwórz ofertę", style=discord.ButtonStyle.link, url=link)))
        container.add_item(discord.ui.ActionRow(
            VacationButton("add", "Dodaj propozycję", discord.ButtonStyle.success),
            VacationButton("edit", "Edytuj", discord.ButtonStyle.primary, selected is None),
            VacationButton("link", "Link oferty", disabled=selected is None),
            VacationButton("delete", "Usuń", discord.ButtonStyle.danger, selected is None),
            VacationButton("refresh", "Odśwież")))
        container.add_item(discord.ui.TextDisplay(f"-# Strona {self.page + 1} z {pages}"))
        container.add_item(discord.ui.ActionRow(
            VacationButton("previous", "Poprzednia", disabled=self.page == 0),
            VacationButton("next", "Następna", disabled=self.page == pages - 1)))
        self.footer = discord.ui.TextDisplay("-# Panel wygasa po 10 minutach bezczynności.")
        container.add_item(self.footer)
        self.add_item(container)

    async def refresh(self, interaction):
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=MENTIONS)

    async def action(self, interaction, action):
        records = load_wakacje()
        selected = next((item for item in records if item["id"] == self.selected_id), None)
        if action == "add":
            await interaction.response.send_modal(VacationModal(self))
            return
        if action in ("edit", "link", "delete") and selected is None:
            await reply(interaction, "Propozycja została usunięta. Odśwież panel.")
            return
        if action in ("edit", "link"):
            await interaction.response.send_modal(VacationModal(self, selected, link_only=action == "link"))
            return
        if action == "delete":
            records.remove(selected)
            save_wakacje(records)
            self.selected_id = None
        elif action in ("previous", "next"):
            self.page += 1 if action == "next" else -1
            self.selected_id = None
            self.detail_page = 0
        elif action in ("detail_previous", "detail_next"):
            self.detail_page += 1 if action == "detail_next" else -1
        await self.refresh(interaction)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await reply(interaction, "Otwórz własny panel przez /wakacje.")
        return False

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        self.footer.content = "-# Panel wygasł. Otwórz nowy przez /wakacje."
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error("Vacation panel failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zaktualizować panelu. Spróbuj ponownie.")


async def setup_wakacje_commands(client: discord.Client, tree: app_commands.CommandTree):
    @tree.command(name="wakacje", description="Panel propozycji wyjazdów: przeglądaj, dodawaj, edytuj i usuwaj",
                  guild=discord.Object(id=GUILD_ID))
    async def wakacje(interaction: discord.Interaction):
        panel = VacationPanel(interaction.user.id)
        await interaction.response.send_message(view=panel, allowed_mentions=MENTIONS)
        panel.message = await interaction.original_response()
