import json
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
    with open(SIEROTY_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


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
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def build_sieroty_success_embed(player_data, real_nick, date_str, target_stats, match_id):
    avatar_url = player_data.get("avatar") or "https://www.faceit.com/static/img/avatar.png"
    kills = int(target_stats.get("kills", 0))
    deaths = int(target_stats.get("deaths", 0))
    assists = int(target_stats.get("assists", 0))
    hs = int(target_stats.get("headshots", 0))
    adr = target_stats.get("adr", "0")
    kd_ratio = kills / deaths if deaths > 0 else float(kills)
    lobby_link = f"https://www.faceit.com/en/cs2/room/{match_id}/scoreboard"

    embed = discord.Embed(
        title="🤡 Dodano do listy sierot",
        description=f"**{real_nick}** | {date_str}",
        color=discord.Color.orange(),
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="K/D/A", value=f"{kills}/{deaths}/{assists}", inline=True)
    embed.add_field(name="K/D", value=f"{kd_ratio:.2f}", inline=True)
    embed.add_field(name="ADR", value=str(adr), inline=True)
    embed.add_field(name="HS", value=f"{hs}%", inline=True)
    embed.add_field(name="", value=f"[🔗 Lobby]({lobby_link})", inline=False)
    return embed


def load_sieroty_ranking():
    if os.path.exists(SIEROTY_RANKING_FILE):
        try:
            with open(SIEROTY_RANKING_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            pass
    return []


def save_sieroty_ranking(data):
    with open(SIEROTY_RANKING_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def build_sieroty_list_views(entries, summary, include_image):
    """Buduje strony listy jako Components V2, z podsumowaniem na ostatniej stronie."""
    header = "# Ściana Wstydu\nSortowane po najniższym ADR."
    player_pages = [[]]
    page_length = 0
    max_player_text_length = 3200

    for entry_text in entries:
        additional_length = len(entry_text) + (2 if player_pages[-1] else 0)
        if player_pages[-1] and page_length + additional_length > max_player_text_length:
            player_pages.append([])
            page_length = 0
            additional_length = len(entry_text)
        player_pages[-1].append(entry_text)
        page_length += additional_length

    # Zostawiamy statystyki razem na końcu; w razie braku miejsca dostają własną stronę.
    last_page_length = page_length + (2 if player_pages[-1] else 0) + len(summary)
    if last_page_length > 3900 and player_pages[-1]:
        player_pages.append([])

    views = []
    for page_index, page_entries in enumerate(player_pages):
        view = discord.ui.LayoutView(timeout=None)
        children = []

        if page_index == 0 and include_image:
            children.append(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem("attachment://sciana_wstydu.png")
                )
            )
        elif page_index == 0:
            children.append(discord.ui.TextDisplay(header))
        else:
            children.append(discord.ui.TextDisplay("## Ściana Wstydu — ciąg dalszy"))

        if page_entries:
            children.append(discord.ui.TextDisplay("\n".join(page_entries)))

        if page_index == len(player_pages) - 1:
            children.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            children.append(discord.ui.TextDisplay(summary))

        view.add_item(discord.ui.Container(*children, accent_color=discord.Color.orange()))
        views.append(view)

    return views


def register_sieroty_commands(tree, guild, faceit_nick_autocomplete):
    import faceit_utils as fu

    sieroty_group = app_commands.Group(name="sieroty", description="Ściana wstydu (najgorsze mecze)")

    @sieroty_group.command(name="lista", description="Wyświetla listę najgorszych meczy")
    async def sieroty_lista(interaction: discord.Interaction):
        sieroty_data = load_sieroty()
        if not sieroty_data:
            await interaction.response.send_message("🐣 Lista sierot jest pusta! Wszyscy grają jak szefowie.", ephemeral=True)
            return

        # Lista jest dopisywana chronologicznie przez /sieroty dodaj.
        last_sierota = sieroty_data[-1]

        try:
            sieroty_data.sort(key=lambda x: float(x.get("adr", 999)))
        except ValueError:
            pass

        previous_ranking = load_sieroty_ranking()
        previous_lookup = {get_sieroty_entry_key(entry): entry for entry in previous_ranking}
        previous_map = {}
        for idx, entry in enumerate(previous_ranking):
            key = get_sieroty_entry_key(entry)
            previous_map[key] = idx

        sciana_wstydu_image = None
        if os.path.exists(SIEROTY_WSTYDU_IMAGE_FILE):
            sciana_wstydu_image = discord.File(SIEROTY_WSTYDU_IMAGE_FILE, filename="sciana_wstydu.png")

        adr_width = max(len(str(entry.get("adr"))) for entry in sieroty_data)
        kda_width = max(
            len(str(entry.get("kda", entry.get("kd", "N/A"))))
            for entry in sieroty_data
        )
        kd_width = max(len(str(get_sieroty_kd_value(entry))) for entry in sieroty_data)
        date_width = max(len(str(entry.get("date", ""))) for entry in sieroty_data)
        position_change_width = 2

        player_texts = []
        for index, entry in enumerate(sieroty_data):
            if index == 0:
                rank_prefix = "🥇"
            elif index == 1:
                rank_prefix = "🥈"
            elif index == 2:
                rank_prefix = "🥉"
            else:
                rank_prefix = f"{index + 1}."

            position_change = ""
            key = get_sieroty_entry_key(entry)
            if key in previous_map:
                prev_pos = previous_map[key]
                if prev_pos > index:
                    position_change = "⬆️"
                elif prev_pos < index:
                    position_change = "⬇️"
                else:
                    position_change = "➖"
            else:
                position_change = "🆕"

            kda_val = str(entry.get("kda", entry.get("kd", "N/A")))
            kd_val = get_sieroty_kd_value(entry)
            adr_val = str(entry.get("adr"))
            date_val = str(entry.get("date", ""))

            lobby_link = get_sieroty_lobby_link(entry, previous_lookup)
            if lobby_link:
                lobby_text = f"[🔗]({lobby_link})"
            else:
                lobby_text = "🔗: brak"
            player_nick = entry["nick"]
            faceit_link = f"https://www.faceit.com/en/players/{quote(player_nick, safe='')}"
            player_texts.append(
                f"{rank_prefix} [**{player_nick}**]({faceit_link})\n"
                f"`{date_val:<{date_width}} | {position_change:<{position_change_width}} | "
                f"ADR: {adr_val:<{adr_width}} | K/D/A: {kda_val:<{kda_width}} | K/D: {kd_val:>{kd_width}}` · {lobby_text}"
            )

        nicks = [entry["nick"] for entry in sieroty_data]
        if nicks:
            common = Counter(nicks).most_common(1)
            freq_str = f"{common[0][0]} ({common[0][1]}x)"
        else:
            freq_str = "Brak"

        adr_entries = [
            (get_sieroty_numeric_value(entry.get("adr")), entry)
            for entry in sieroty_data
        ]
        adr_entries = [(value, entry) for value, entry in adr_entries if value is not None]
        if adr_entries:
            lowest_adr = min(value for value, _ in adr_entries)
            lowest_adr_nicks = list(dict.fromkeys(entry["nick"] for value, entry in adr_entries if value == lowest_adr))
            lowest_adr_str = f"{', '.join(lowest_adr_nicks)} ({lowest_adr:g})"
        else:
            lowest_adr_str = "Brak danych"

        kd_entries = [
            (get_sieroty_numeric_value(get_sieroty_kd_value(entry)), entry)
            for entry in sieroty_data
        ]
        kd_entries = [(value, entry) for value, entry in kd_entries if value is not None]
        if kd_entries:
            lowest_kd = min(value for value, _ in kd_entries)
            lowest_kd_nicks = list(dict.fromkeys(entry["nick"] for value, entry in kd_entries if value == lowest_kd))
            lowest_kd_str = f"{', '.join(lowest_kd_nicks)} ({lowest_kd:.2f})"
        else:
            lowest_kd_str = "Brak danych"

        summary = (
            f"-# 🤡 **Stały klient:** {freq_str}\n"
            f"-# 📉 **Najniższy ADR:** {lowest_adr_str}\n"
            f"-# 📉 **Najniższe K/D:** {lowest_kd_str}\n"
            f"-# 🧱 **Ostatnia ścianka:** {last_sierota.get('nick', 'Nieznany')} - {last_sierota.get('date', 'brak daty')}"
        )
        views = build_sieroty_list_views(player_texts, summary, include_image=sciana_wstydu_image is not None)

        save_sieroty_ranking(sieroty_data)

        await interaction.response.send_message(view=views[0], file=sciana_wstydu_image)
        for view in views[1:]:
            await interaction.followup.send(view=view)

    @sieroty_group.command(name="dodaj", description="Dodaje ostatni mecz gracza do listy sierot")
    @app_commands.describe(nick="Nick gracza")
    @app_commands.autocomplete(nick=faceit_nick_autocomplete)
    async def sieroty_dodaj(interaction: discord.Interaction, nick: str):
        await interaction.response.defer()

        player_data = fu.get_faceit_player_data(nick)
        if not player_data:
            await interaction.followup.send(f"❌ Nie znaleziono gracza **{nick}**.", ephemeral=True)
            return

        pid = player_data["player_id"]
        matches = fu.get_faceit_player_matches(pid, limit=1)
        if not matches:
            await interaction.followup.send(f"❌ Brak meczy dla gracza **{nick}**.", ephemeral=True)
            return

        last_match = matches[0]
        match_id = last_match.get("match_id") if last_match.get("match_id") else last_match.get("stats", {}).get("Match Id")

        if not match_id:
            await interaction.followup.send("❌ Nie udało się pobrać ID ostatniego meczu.", ephemeral=True)
            return

        details = fu.get_faceit_match_details(match_id)
        if not details:
            await interaction.followup.send("❌ Błąd pobierania szczegółów meczu.", ephemeral=True)
            return

        target_stats = None
        real_nick = player_data["nickname"]

        for team in details["teams"].values():
            for player in team["players"]:
                if player["nickname"] == real_nick:
                    target_stats = player
                    break
            if target_stats:
                break

        if not target_stats:
            await interaction.followup.send("❌ Nie znaleziono statystyk gracza w tym meczu.", ephemeral=True)
            return

        months_pl = {
            1: "stycznia",
            2: "lutego",
            3: "marca",
            4: "kwietnia",
            5: "maja",
            6: "czerwca",
            7: "lipca",
            8: "sierpnia",
            9: "września",
            10: "października",
            11: "listopada",
            12: "grudnia",
        }
        now = datetime.now()
        date_str = f"{now.day} {months_pl[now.month]} {now.year}"

        kills = target_stats["kills"]
        deaths = target_stats["deaths"]
        assists = target_stats["assists"]
        kd_ratio = kills / deaths if deaths > 0 else kills
        kda_str = f"{kills}/{deaths}/{assists}"

        entry = {
            "nick": real_nick,
            "date": date_str,
            "adr": f"{float(target_stats.get('adr', 0)):.0f}",
            "kda": kda_str,
            "kd": f"{kd_ratio:.2f}",
            "hs": str(target_stats["headshots"]),
            "match_id": match_id,
            "lobby_link": f"https://www.faceit.com/en/cs2/room/{match_id}/scoreboard",
        }

        data = load_sieroty()
        data.append(entry)
        save_sieroty(data)

        success_embed = build_sieroty_success_embed(player_data, real_nick, date_str, target_stats, match_id)
        await interaction.followup.send(embed=success_embed)

    @sieroty_group.command(name="usun", description="Usuwa ostatni wpis gracza z listy sierot")
    @app_commands.describe(nick="Nick gracza")
    @app_commands.autocomplete(nick=faceit_nick_autocomplete)
    async def sieroty_usun(interaction: discord.Interaction, nick: str):
        data = load_sieroty()
        if not data:
            await interaction.response.send_message("Lista jest pusta.", ephemeral=True)
            return

        index_to_remove = -1
        for i in range(len(data) - 1, -1, -1):
            if data[i]["nick"].lower() == nick.lower():
                index_to_remove = i
                break

        if index_to_remove != -1:
            removed = data.pop(index_to_remove)
            save_sieroty(data)
            await interaction.response.send_message(
                f"🗑️ Usunięto wpis dla **{removed['nick']}** z dnia {removed['date']}."
            )
        else:
            await interaction.response.send_message(f"❌ Nie znaleziono wpisów dla gracza **{nick}**.", ephemeral=True)

    tree.add_command(sieroty_group, guild=guild)
