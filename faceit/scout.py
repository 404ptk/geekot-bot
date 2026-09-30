"""Discord command for tracking one live FACEIT match at a time."""

import asyncio
import re
from itertools import zip_longest

import discord
import requests
from discord import app_commands
from faceit.common import get_faceit_level_badge


POLL_INTERVAL_SECONDS = 60
MATCH_ID_PATTERN = re.compile(r"1-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
MATCH_URL = "https://www.faceit.com/api/match/v4/match/{match_id}"
_scout_reserved = False
_scout_task: asyncio.Task | None = None


def extract_match_id(value: str) -> str | None:
    match = MATCH_ID_PATTERN.search((value or "").strip())
    return match.group(0) if match else None


def _request_match_payload(match_id: str) -> dict:
    response = requests.get(
        MATCH_URL.format(match_id=match_id),
        headers={"User-Agent": "FaceitScoreScout/1.0", "Accept": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    body = response.json()
    payload = body.get("payload")
    if not isinstance(payload, dict) or payload.get("id") != match_id:
        raise ValueError("Nie znaleziono meczu o podanym ID.")
    return payload


def _extract_score(payload: dict) -> dict:
    factions = (payload.get("summaryResults") or {}).get("factions") or {}
    score1 = (factions.get("faction1") or {}).get("score")
    score2 = (factions.get("faction2") or {}).get("score")
    if score1 is None or score2 is None:
        results = payload.get("results") or []
        if results:
            result_factions = results[-1].get("factions") or {}
            score1 = (result_factions.get("faction1") or {}).get("score")
            score2 = (result_factions.get("faction2") or {}).get("score")
    if score1 is None or score2 is None:
        raise ValueError("Mecz znaleziony, ale wynik nie jest jeszcze dostępny.")

    return {
        "score1": int(score1),
        "score2": int(score2),
        "status": str(payload.get("status", payload.get("state", "UNKNOWN"))).upper(),
    }


def fetch_match_data(match_id: str) -> dict:
    payload = _request_match_payload(match_id)
    teams = payload.get("teams") or {}
    data = {
        "team1": (teams.get("faction1") or {}).get("name", "Drużyna 1"),
        "team2": (teams.get("faction2") or {}).get("name", "Drużyna 2"),
        "roster1": (teams.get("faction1") or {}).get("roster") or [],
        "roster2": (teams.get("faction2") or {}).get("roster") or [],
    }
    data.update(_extract_score(payload))
    return data


def fetch_match_score(match_id: str) -> dict:
    """Fetch only the fields that change; roster data stays cached from startup."""
    return _extract_score(_request_match_payload(match_id))


def match_is_finished(data: dict) -> bool:
    status = data["status"]
    if status in {"FINISHED", "COMPLETED", "ENDED", "ABORTED", "CANCELLED", "CANCELED"}:
        return True

    score1, score2 = data["score1"], data["score2"]
    high, low = max(score1, score2), min(score1, score2)
    # Regulation: first to 13, unless the match reached 12:12.
    if high == 13 and low < 12:
        return True
    # Overtime blocks start at 12:12, then 15:15, 18:18, ...;
    # a team wins a block by reaching four rounds in that block.
    if low >= 12:
        overtime_start = 12 + ((low - 12) // 3) * 3
        return high - overtime_start >= 4
    return False


def format_rosters(roster1: list[dict], roster2: list[dict], guild) -> str:
    def format_player(player):
        if player is None:
            return {"badge": "", "nickname": "—", "elo": "", "badge_width": 0}

        nickname = str(player.get("nickname") or "Nieznany").replace("`", "ˋ")
        try:
            level = int(player.get("gameSkillLevel"))
        except (TypeError, ValueError):
            level = 0
        badge = get_faceit_level_badge(guild, level)
        elo = player.get("elo")
        badge_width = 2 if badge.startswith("<") or badge == "❓" else len(badge)
        return {
            "badge": badge,
            "nickname": nickname,
            "elo": str(elo) if elo is not None else "—",
            "badge_width": badge_width,
        }

    cells1 = [format_player(player) for player in roster1]
    cells2 = [format_player(player) for player in roster2]
    if not cells1 and not cells2:
        return "Brak danych o składach."

    nbsp = "\u00a0"

    def team_widths(cells):
        if not cells:
            cells = [format_player(None)]
        nickname_width = max(len(cell["nickname"]) for cell in cells)
        elo_width = max(len(cell["elo"]) for cell in cells)
        total_width = max(
            cell["badge_width"] + 1 + nickname_width + 1 + elo_width
            for cell in cells
        )
        return nickname_width, elo_width, total_width

    left_nick_width, left_elo_width, left_width = team_widths(cells1)
    right_nick_width, right_elo_width, _ = team_widths(cells2)

    def render_cell(cell, nickname_width, elo_width, total_width=None):
        nickname = cell["nickname"] + nbsp * (nickname_width - len(cell["nickname"]))
        elo = cell["elo"]
        label = f"{nickname} {elo}{nbsp * (elo_width - len(elo))}"
        cell_width = cell["badge_width"] + 1 + nickname_width + 1 + elo_width
        if total_width is not None:
            label += nbsp * (total_width - cell_width)
        if cell["badge"]:
            return f"{cell['badge']} `{label}`"
        return f"`{label}`"

    lines = []
    for player1, player2 in zip_longest(cells1, cells2):
        left = player1 or format_player(None)
        right = player2 or format_player(None)
        left_cell = render_cell(left, left_nick_width, left_elo_width, left_width)
        right_cell = render_cell(right, right_nick_width, right_elo_width)
        lines.append(f"{left_cell} | {right_cell}")

    return "\n".join(lines)


def build_scout_view(
    data: dict,
    *,
    guild=None,
    finished: bool = False,
    stopped: bool = False,
) -> discord.ui.LayoutView:
    team1, team2 = data["team1"], data["team2"]
    score1, score2 = str(data["score1"]), str(data["score2"])
    # The label row has two full-width spaces on both sides of "vs".
    gap_to_vs = 4
    gap_from_vs = 4
    team1_center = len(team1) / 2
    vs_center = len(team1) + gap_to_vs + len("vs") / 2
    team2_center = len(team1) + gap_to_vs + len("vs") + gap_from_vs + len(team2) / 2
    score1_padding = max(0, round(team1_center - len(score1) / 2))
    colon_gap = max(0, round(vs_center - 0.5 - score1_padding - len(score1)))
    score2_padding = max(0, round(team2_center - len(score2) / 2 - vs_center - 0.5))
    nbsp = "\u00a0"
    # The zero-width character prevents Discord's heading parser from trimming
    # the leading non-breaking spaces used to align each score under its team.
    scoreline = f"## \u200b{nbsp * score1_padding}{score1}{nbsp * colon_gap}:{nbsp * score2_padding}{score2}"
    if stopped:
        heading = "## ⏸️ Śledzenie zatrzymane"
        footer = "Tracker wyłączony ręcznie"
        color = discord.Color.dark_grey()
    elif finished:
        heading = "## 🏁 Mecz zakończony"
        footer = "Wynik końcowy"
        color = discord.Color.green()
    else:
        heading = "## 🔴 FACEIT • wynik na żywo"
        footer = "Wynik sprawdzany co minutę"
        color = discord.Color.orange()

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(heading),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(f"**{team1}**　　**vs**　　**{team2}**"),
            discord.ui.TextDisplay(scoreline),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"{format_rosters(data.get('roster1') or [], data.get('roster2') or [], guild)}"
            ),
            discord.ui.TextDisplay(f"-# {footer} · status: `{data['status']}`"),
            accent_color=color,
        )
    )
    return view


async def track_match(
    message: discord.WebhookMessage,
    match_id: str,
    initial_data: dict,
    guild,
) -> None:
    global _scout_reserved, _scout_task
    previous_score = (initial_data["score1"], initial_data["score2"])
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            try:
                updated = await asyncio.to_thread(fetch_match_score, match_id)
            except (requests.RequestException, ValueError, requests.JSONDecodeError):
                # Retry only at the next regular minute tick after transient errors.
                continue

            score = (updated["score1"], updated["score2"])
            finished = match_is_finished(updated)
            if score != previous_score or finished:
                view_data = {**initial_data, **updated}
                await message.edit(view=build_scout_view(view_data, guild=guild, finished=finished))
                previous_score = score
            if finished:
                break
    except asyncio.CancelledError:
        try:
            await message.edit(view=build_scout_view(initial_data, guild=guild, stopped=True))
        except discord.HTTPException:
            pass
        raise
    except discord.HTTPException:
        # Stop if Discord no longer accepts tracking updates in this channel.
        pass
    finally:
        _scout_reserved = False
        if _scout_task is asyncio.current_task():
            _scout_task = None


def register_scout_command(tree: app_commands.CommandTree, guild: discord.Object) -> None:
    @tree.command(
        name="scout",
        description="Śledzi wynik meczu FACEIT i publikuje zmiany rund",
        guild=guild,
    )
    @app_commands.describe(id_meczu="ID meczu FACEIT (np. 1-...) albo `clear`, aby zatrzymać śledzenie")
    async def scout(interaction: discord.Interaction, id_meczu: str):
        global _scout_reserved, _scout_task

        if id_meczu.strip().lower() == "clear":
            task = _scout_task
            if task is None or task.done():
                message = (
                    "⏳ Tracker właśnie startuje. Spróbuj `/scout clear` ponownie za chwilę."
                    if _scout_reserved
                    else "ℹ️ Żaden mecz nie jest teraz śledzony."
                )
                await interaction.response.send_message(message, ephemeral=True)
                return

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if _scout_task is task:
                _scout_task = None
                _scout_reserved = False
            await interaction.response.send_message("🛑 Zatrzymano śledzenie meczu.", ephemeral=True)
            return

        if _scout_reserved:
            await interaction.response.send_message(
                "⏳ Inny mecz jest już śledzony. Na całym bocie może działać tylko jeden `/scout`.",
                ephemeral=True,
            )
            return

        match_id = extract_match_id(id_meczu)
        if not match_id:
            await interaction.response.send_message(
                "❌ Nieprawidłowe ID meczu. Wklej ID w formacie `1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.",
                ephemeral=True,
            )
            return

        # Reserve before the first await so concurrent invocations cannot start
        # a second poller while this one is checking the match.
        _scout_reserved = True
        tracker_started = False
        try:
            await interaction.response.defer()
            try:
                data = await asyncio.to_thread(fetch_match_data, match_id)
            except requests.RequestException as exc:
                await interaction.followup.send(
                    f"❌ Nie udało się pobrać meczu z FACEIT: `{exc}`", ephemeral=True
                )
                return
            except (ValueError, requests.JSONDecodeError) as exc:
                await interaction.followup.send(f"❌ {exc}", ephemeral=True)
                return

            finished = match_is_finished(data)
            message = await interaction.followup.send(
                view=build_scout_view(data, guild=interaction.guild, finished=finished), wait=True
            )
            if not finished:
                _scout_task = asyncio.create_task(
                    track_match(message, match_id, data, interaction.guild)
                )
                tracker_started = True
        finally:
            if not tracker_started:
                _scout_reserved = False
