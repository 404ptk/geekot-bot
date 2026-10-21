import asyncio
import json
import logging
import math
from pathlib import Path
import os
from collections import Counter
from datetime import datetime
from urllib.parse import quote

import discord
from discord import app_commands

SIEROTY_FILE = "txt/sieroty.json"
SIEROTY_RANKING_FILE = "txt/sieroty_ranking.json"
SIEROTY_WSTYDU_IMAGE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "images", "ranking", "sciana_wstydu.png"
)


def load_sieroty():
    if os.path.exists(SIEROTY_FILE):
        try:
            with open(SIEROTY_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def save_sieroty(data):
    save_json(SIEROTY_FILE, data)


def get_sieroty_entry_key(entry):
    return f"{entry.get('match_id')}_{entry.get('nick')}_{entry.get('date')}"


def get_sieroty_lobby_link(entry, ranking_lookup=None):
    if ranking_lookup:
        ranking_entry = ranking_lookup.get(get_sieroty_entry_key(entry))
        if ranking_entry:
            lobby_link = str(ranking_entry.get("lobby_link", "")).strip()
            if lobby_link:
                return lobby_link

            match_id = str(ranking_entry.get("match_id", "")).strip()
            if match_id and match_id.lower() != "manual":
                return f"https://www.faceit.com/en/cs2/room/{match_id}/scoreboard"

    lobby_link = str(entry.get("lobby_link", "")).strip()
    if lobby_link:
        return lobby_link

    match_id = str(entry.get("match_id", "")).strip()
    if match_id and match_id.lower() != "manual":
        return f"https://www.faceit.com/en/cs2/room/{match_id}/scoreboard"

    return ""


def get_sieroty_kd_value(entry):
    kd_value = str(entry.get("kd", "")).strip()
    if kd_value and kd_value.upper() != "N/A":
        return kd_value

    kda_value = str(entry.get("kda", "")).strip()
    parts = kda_value.split("/")
    if len(parts) >= 2:
        try:
            kills = float(parts[0])
            deaths = float(parts[1])
            if deaths > 0:
                return f"{kills / deaths:.2f}"
            return f"{kills:.2f}"
        except ValueError:
            pass

    return "N/A"


def get_sieroty_numeric_value(value):
    try:
        number = float(str(value).strip().replace(",", "."))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def load_sieroty_ranking():
    if os.path.exists(SIEROTY_RANKING_FILE):
        try:
            with open(SIEROTY_RANKING_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            pass
    return []


def save_sieroty_ranking(data):
    save_json(SIEROTY_RANKING_FILE, data)


PAGE_SIZE = 5
MENTIONS = discord.AllowedMentions.none()
logger = logging.getLogger(__name__)


def save_json(filename, data):
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    temporary.replace(path)


def escaped(value, limit=100):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def adr_sort_key(entry):
    value = get_sieroty_numeric_value(entry.get("adr"))
    return value if value is not None else float("inf")


def fetch_latest_entry(nick):
    """Fetch FACEIT data in a worker; persistence stays on the event loop."""
    import faceit_utils as fu

    player = fu.get_faceit_player_data(nick)
    if not player:
        raise ValueError("Nie znaleziono gracza o podanym nicku.")
    matches = fu.get_faceit_player_matches(player["player_id"], limit=1)
    if not matches:
        raise ValueError("Ten gracz nie ma ostatnich meczów.")
    match_id = matches[0].get("match_id") or matches[0].get("stats", {}).get("Match Id")
    if not match_id:
        raise ValueError("Nie udało się pobrać ID ostatniego meczu.")
    details = fu.get_faceit_match_details(match_id)
    if not details:
        raise ValueError("Nie udało się pobrać statystyk ostatniego meczu.")
    real_nick = player["nickname"]
    stats = next((item for team in details.get("teams", {}).values()
                  for item in team.get("players", [])
                  if item.get("nickname", "").casefold() == real_nick.casefold()), None)
    if stats is None:
        raise ValueError("Nie znaleziono statystyk gracza w ostatnim meczu.")
    months = ("stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
              "lipca", "sierpnia", "września", "października", "listopada", "grudnia")
    now = datetime.now()
    kills, deaths, assists = (int(stats.get(key, 0)) for key in ("kills", "deaths", "assists"))
    return {
        "nick": real_nick, "date": f"{now.day} {months[now.month - 1]} {now.year}",
        "adr": f"{float(stats.get('adr', 0)):.0f}",
        "kda": f"{kills}/{deaths}/{assists}",
        "kd": f"{kills / deaths if deaths else kills:.2f}",
        "hs": str(stats.get("headshots", 0)), "match_id": match_id,
        "lobby_link": f"https://www.faceit.com/en/cs2/room/{match_id}/scoreboard",
    }


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=MENTIONS)


class WallButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction):
        await self.view.action(interaction, self.action)


class WallSelect(discord.ui.Select):
    def __init__(self, panel):
        self.snapshot = panel.data.copy()
        start = panel.page * PAGE_SIZE
        super().__init__(placeholder="Wybierz wpis, aby zobaczyć szczegóły lub go usunąć", options=[
            discord.SelectOption(
                label=f"{rank + 1}. {entry.get('nick', '?')} · ADR {entry.get('adr', '?')}"[:100],
                description=str(entry.get("date", "Brak daty"))[:100], value=str(index),
                default=panel.selected == index,
            ) for rank, (index, entry) in enumerate(panel.ranking[start:start + PAGE_SIZE], start)
        ])

    async def callback(self, interaction):
        panel = self.view
        if load_sieroty() != self.snapshot:
            panel.selected = None
            panel.notice = "Lista się zmieniła. Wybierz wpis ponownie."
        else:
            panel.selected = int(self.values[0])
            panel.selection_snapshot = self.snapshot.copy()
            panel.notice = ""
        await panel.refresh(interaction)


class AddWallModal(discord.ui.Modal, title="Dodaj gracza na ściankę"):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.nick = discord.ui.TextInput(label="Nick FACEIT", min_length=1, max_length=100)
        self.add_item(self.nick)

    async def on_submit(self, interaction):
        panel = self.panel
        if not await panel.interaction_check(interaction):
            return
        nick = self.nick.value.strip()
        if not nick:
            await reply(interaction, "Podaj nick gracza FACEIT.")
            return
        # A modal submitted from the panel can defer an edit of that same message.
        await interaction.response.defer()
        try:
            entry = await asyncio.wait_for(asyncio.to_thread(fetch_latest_entry, nick), timeout=30)
        except ValueError as exc:
            await reply(interaction, str(exc))
            return
        except TimeoutError:
            await reply(interaction, "FACEIT nie odpowiedział na czas. Spróbuj ponownie.")
            return
        if panel.is_finished():
            await reply(interaction, "Panel wygasł. Otwórz nowy przez /scianka.")
            return
        # Reload after the network call so additions/deletions from other panels survive.
        data = load_sieroty()
        data.append(entry)
        save_sieroty(data)
        panel.selected = len(data) - 1
        panel.selection_snapshot = data.copy()
        ranked = sorted(enumerate(data), key=lambda pair: adr_sort_key(pair[1]))
        panel.page = next(rank for rank, (index, _) in enumerate(ranked)
                          if index == panel.selected) // PAGE_SIZE
        panel.notice = f"🤡 Dodano ostatni mecz gracza **{escaped(entry['nick'])}**."
        panel.render()
        await interaction.edit_original_response(view=panel, allowed_mentions=MENTIONS)

    async def on_error(self, interaction, error):
        logger.error("Wall form failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się dodać meczu. Spróbuj ponownie.")


class WallPanel(discord.ui.LayoutView):
    def __init__(self, owner_id, include_image=False):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.include_image = include_image
        self.message = None
        self.page = 0
        self.selected = None
        self.selection_snapshot = None
        self.notice = ""
        previous = load_sieroty_ranking()
        self.previous_positions = {get_sieroty_entry_key(entry): index for index, entry in enumerate(previous)}
        self.previous_lookup = {get_sieroty_entry_key(entry): entry for entry in previous}
        self.render()

    def summary(self):
        if not self.data:
            return "Wszyscy grają jak szefowie."
        nick, count = Counter(str(entry.get("nick", "?")) for entry in self.data).most_common(1)[0]
        lines = [f"-# 🤡 **Stały klient:** {escaped(nick)} ({count}x)"]
        for label, getter, fmt in (
            ("Najniższy ADR", lambda entry: entry.get("adr"), "g"),
            ("Najniższe K/D", get_sieroty_kd_value, ".2f"),
        ):
            values = [(get_sieroty_numeric_value(getter(entry)), entry) for entry in self.data]
            values = [(value, entry) for value, entry in values if value is not None]
            if values:
                lowest = min(value for value, _ in values)
                nicks = ", ".join(dict.fromkeys(str(entry.get("nick", "?"))
                                               for value, entry in values if value == lowest))
                text = f"{escaped(nicks, 160)} ({lowest:{fmt}})"
            else:
                text = "Brak danych"
            lines.append(f"-# 📉 **{label}:** {text}")
        latest = self.data[-1]
        lines.append(f"-# 🧱 **Ostatnia ścianka:** {escaped(latest.get('nick', '?'))} · {escaped(latest.get('date', '?'))}")
        return "\n".join(lines)

    def render(self):
        self.data = load_sieroty()
        self.ranking = sorted(enumerate(self.data), key=lambda pair: adr_sort_key(pair[1]))
        ranked_entries = [entry for _, entry in self.ranking]
        if ranked_entries != load_sieroty_ranking():
            save_sieroty_ranking(ranked_entries)
        if self.data != self.selection_snapshot:
            self.selected = None
        pages = max(1, (len(self.data) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        self.clear_items()
        container = discord.ui.Container(accent_color=0xE67E22)
        if self.include_image:
            container.add_item(discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://sciana_wstydu.png")))
        container.add_item(discord.ui.TextDisplay(
            f"## 🧱 Ścianka wstydu\n-# Najgorsze mecze według ADR · Wpisów: {len(self.data)}"))
        if self.notice:
            container.add_item(discord.ui.TextDisplay(self.notice))
        container.add_item(discord.ui.Separator())
        if not self.data:
            container.add_item(discord.ui.TextDisplay("Ścianka jest pusta. Dodaj ostatni mecz gracza przyciskiem poniżej."))
        start = self.page * PAGE_SIZE
        for rank, (index, entry) in enumerate(self.ranking[start:start + PAGE_SIZE], start):
            key = get_sieroty_entry_key(entry)
            previous = self.previous_positions.get(key)
            change = "🆕" if previous is None else "⬆️" if previous > rank else "⬇️" if previous < rank else "➖"
            medal = ("🥇", "🥈", "🥉")[rank] if rank < 3 else f"{rank + 1}."
            nick = str(entry.get("nick", "?"))
            profile = f"https://www.faceit.com/en/players/{quote(nick, safe='')}"
            lobby = get_sieroty_lobby_link(entry, self.previous_lookup)
            link = f" · [🔗 Lobby]({lobby})" if lobby.startswith("https://www.faceit.com/") else ""
            text = (f"{medal} [**{escaped(nick)}**]({profile}) · {escaped(entry.get('date', '?'), 60)} {change}\n"
                    f"-# ADR: **{escaped(entry.get('adr', 'N/A'), 15)}**"
                    f" · K/D/A: {escaped(entry.get('kda', 'N/A'), 25)}"
                    f" · K/D: {escaped(get_sieroty_kd_value(entry), 15)}{link}")
            container.add_item(discord.ui.TextDisplay(text))
        if self.data:
            container.add_item(discord.ui.ActionRow(WallSelect(self)))
        if self.selected is not None:
            entry = self.data[self.selected]
            container.add_item(discord.ui.TextDisplay(
                f"### Wybrany wpis: {escaped(entry.get('nick', '?'))}\n"
                f"{escaped(entry.get('date', '?'), 60)} · HS: {escaped(entry.get('hs', 'N/A'), 15)}%"))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(self.summary()))
        container.add_item(discord.ui.ActionRow(
            WallButton("add", "Dodaj ostatni mecz", discord.ButtonStyle.success),
            WallButton("delete", "Usuń wpis", discord.ButtonStyle.danger, self.selected is None),
            WallButton("refresh", "Odśwież")))
        container.add_item(discord.ui.TextDisplay(f"-# Strona {self.page + 1} z {pages}"))
        container.add_item(discord.ui.ActionRow(
            WallButton("previous", "Poprzednia", disabled=self.page == 0),
            WallButton("next", "Następna", disabled=self.page == pages - 1)))
        self.footer = discord.ui.TextDisplay("-# Panel wygasa po 10 minutach bezczynności.")
        container.add_item(self.footer)
        self.add_item(container)

    async def refresh(self, interaction):
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=MENTIONS)

    async def action(self, interaction, action):
        if action == "add":
            await interaction.response.send_modal(AddWallModal(self))
            return
        self.notice = ""
        if action == "delete":
            data = load_sieroty()
            if self.selected is None or data != self.selection_snapshot:
                self.selected = None
                self.notice = "Lista się zmieniła. Wybierz wpis ponownie."
            else:
                removed = data.pop(self.selected)
                save_sieroty(data)
                self.selected = None
                self.notice = f"🗑️ Usunięto wpis gracza **{escaped(removed.get('nick', '?'))}**."
        elif action in ("previous", "next"):
            self.page += 1 if action == "next" else -1
            self.selected = None
        await self.refresh(interaction)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await reply(interaction, "Otwórz własny panel przez /scianka.")
            return False
        if self.is_finished():
            await reply(interaction, "Panel wygasł. Otwórz nowy przez /scianka.")
            return False
        return True

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        self.footer.content = "-# Panel wygasł. Otwórz nowy przez /scianka."
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error("Wall panel failed", exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, "Nie udało się zaktualizować ścianki. Spróbuj ponownie.")


def register_scianka_command(tree, guild):
    @tree.command(name="scianka", description="Ścianka wstydu: przeglądaj, dodawaj ostatnie mecze i usuwaj wpisy", guild=guild)
    async def scianka(interaction: discord.Interaction):
        include_image = os.path.exists(SIEROTY_WSTYDU_IMAGE_FILE)
        panel = WallPanel(interaction.user.id, include_image=include_image)
        kwargs = {"view": panel, "allowed_mentions": MENTIONS}
        if include_image:
            kwargs["file"] = discord.File(SIEROTY_WSTYDU_IMAGE_FILE, filename="sciana_wstydu.png")
        await interaction.response.send_message(**kwargs)
        panel.message = await interaction.original_response()
