import io
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import discord
import requests
from discord.ext import tasks
from PIL import Image, ImageDraw, ImageFont

from faceit.common import format_faceit_form
from config.conf_faceit_live_settings import load_config, DEFAULT_CHANNEL_ID


# Live z grafiką (ten sam kanał co stary embed; odświeżanie co 5 min)
FACEIT_LIVE_IMAGE_STATE_FILE = "txt/discordfaceit_live_image.json"
FACEIT_LIVE_IMAGE_CHANNEL_ID = DEFAULT_CHANNEL_ID
AVATAR_CACHE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "images", "faceit_avatars")
)
AVATAR_CACHE_META_FILE = "txt/faceit_avatar_cache.json"

CLIENT_REF = None
_live_image_lock = None

FC_LVL_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "images", "fc-lvl"))
FACEIT_LOGO_PATH = os.path.join(FC_LVL_DIR, "faceitlogo.webp")
EMOJI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "images", "emoji"))
FIRE_EMOJI_PATH = os.path.join(EMOJI_DIR, "fire.png")
CRY_EMOJI_PATH = os.path.join(EMOJI_DIR, "cry.png")
DEFAULT_AVATAR_URL = "https://www.faceit.com/static/img/avatar.png"

# Faceit-inspired dark palette
COLOR_BG = (22, 22, 24, 255)
COLOR_PANEL = (30, 30, 34, 255)
COLOR_ROW = (38, 38, 44, 255)
COLOR_ROW_ALT = (34, 34, 40, 255)
COLOR_TEXT = (240, 240, 242, 255)
COLOR_MUTED = (140, 140, 150, 255)
COLOR_LABEL = (170, 170, 180, 255)
COLOR_ORANGE = (255, 85, 0, 255)
COLOR_WIN = (57, 211, 83, 255)
COLOR_LOSS = (240, 80, 80, 255)
COLOR_NEUTRAL = (90, 90, 98, 255)
COLOR_GOLD = (255, 200, 87, 255)
COLOR_SILVER = (192, 202, 216, 255)
COLOR_BRONZE = (205, 127, 50, 255)




def load_faceit_live_image_state():
    if os.path.exists(FACEIT_LIVE_IMAGE_STATE_FILE):
        try:
            with open(FACEIT_LIVE_IMAGE_STATE_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_faceit_live_image_state(data):
    os.makedirs(os.path.dirname(FACEIT_LIVE_IMAGE_STATE_FILE) or ".", exist_ok=True)
    with open(FACEIT_LIVE_IMAGE_STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _avatar_cache_key(tracked_nickname: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (tracked_nickname or "unknown"))


def _avatar_cache_path(tracked_nickname: str) -> str:
    return os.path.join(AVATAR_CACHE_DIR, f"{_avatar_cache_key(tracked_nickname)}.png")


def load_avatar_cache_meta() -> dict:
    if os.path.exists(AVATAR_CACHE_META_FILE):
        try:
            with open(AVATAR_CACHE_META_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_avatar_cache_meta(data: dict) -> None:
    os.makedirs(os.path.dirname(AVATAR_CACHE_META_FILE) or ".", exist_ok=True)
    with open(AVATAR_CACHE_META_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def _download_avatar_to_cache(tracked_nickname: str, url: Optional[str]) -> bool:
    """Pobiera avatar i zapisuje lokalnie (kwadratowy PNG). Zwraca True przy sukcesie."""
    os.makedirs(AVATAR_CACHE_DIR, exist_ok=True)
    path = _avatar_cache_path(tracked_nickname)
    if not url:
        return False
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            return False
        raw = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        raw.save(path, format="PNG")
        return True
    except Exception:
        return False


def refresh_avatar_cache_if_needed(player_stats: List[dict], *, force: bool = False) -> None:
    """Odświeża avatary raz dziennie (pierwszy tick po zmianie daty / force)."""
    meta = load_avatar_cache_meta()
    today = _today_str()
    if not force and meta.get("date") == today:
        # upewnij się, że pliki istnieją — brakujące dociągamy bez resetu dnia
        missing = [
            p for p in player_stats
            if not os.path.isfile(_avatar_cache_path(p.get("tracked_nickname") or p.get("nickname") or ""))
        ]
        if not missing:
            return
        for player in missing:
            key = player.get("tracked_nickname") or player.get("nickname") or "unknown"
            _download_avatar_to_cache(key, player.get("avatar"))
        return

    for player in player_stats:
        key = player.get("tracked_nickname") or player.get("nickname") or "unknown"
        _download_avatar_to_cache(key, player.get("avatar"))

    save_avatar_cache_meta({"date": today, "updated_at": datetime.now().isoformat(timespec="seconds")})


def collect_discordfaceit_player_stats():
    import faceit_utils as fu

    player_stats = []

    for nickname in load_config()["players"]:
        player_data = fu.get_faceit_player_data(nickname)
        if player_data:
            player_level = player_data.get("games", {}).get("cs2", {}).get("skill_level", 0)
            player_elo = player_data.get("games", {}).get("cs2", {}).get("faceit_elo", 0)
            pid = player_data.get("player_id")
            avatar = player_data.get("avatar") or DEFAULT_AVATAR_URL

            last_matches_str = "N/A"
            outcomes: List[str] = []
            streak_emoji = ""
            if pid:
                matches = fu.get_faceit_player_matches(pid, limit=5)
                if matches:
                    for match in matches:
                        result = match.get("stats", {}).get("Result")
                        if result == "1":
                            outcomes.append("W")
                        elif result == "0":
                            outcomes.append("L")
                        else:
                            outcomes.append("?")
                    last_matches_str = "/".join(outcomes)

                    if len(outcomes) >= 3:
                        if outcomes[:3] == ["W", "W", "W"]:
                            streak_emoji = " 🔥"
                        elif outcomes[:3] == ["L", "L", "L"]:
                            streak_emoji = " 😭"

            player_stats.append(
                {
                    "tracked_nickname": nickname,
                    "nickname": player_data.get("nickname") or nickname,
                    "level": player_level if isinstance(player_level, int) else 0,
                    "elo": player_elo if isinstance(player_elo, int) else 0,
                    "avatar": avatar,
                    "outcomes": outcomes,
                    "last_matches_raw": last_matches_str,
                    "last_matches": format_faceit_form(outcomes) if outcomes else "⚪",
                    "streak_emoji": streak_emoji,
                }
            )

    player_stats.sort(key=lambda x: (x["elo"], x["level"]), reverse=True)
    return player_stats




def _load_font(size: int, *, weight: str = "regular") -> ImageFont.ImageFont:
    base = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "images", "font"))
    weight = weight.lower()
    inter_map = {
        "bold": "Inter-Bold.ttf",
        "semibold": "Inter-SemiBold.ttf",
        "medium": "Inter-Medium.ttf",
        "regular": "Inter-Regular.ttf",
    }
    preferred = [
        os.path.join(base, "inter", inter_map.get(weight, "Inter-Regular.ttf")),
        os.path.join(base, "inter", "Inter-Medium.ttf"),
        os.path.join(base, "inter", "Inter-Regular.ttf"),
        os.path.join(base, "roboto", "Roboto-Medium.ttf"),
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in preferred:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill=None, outline=None, width: int = 1):
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    except Exception:
        draw.rectangle(box, fill=fill, outline=outline, width=width)


def _circle_crop(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0))
    out.putalpha(mask)
    return out


def _fetch_avatar(url: Optional[str], size: int) -> Image.Image:
    fallback = Image.new("RGBA", (size, size), (58, 58, 66, 255))
    ImageDraw.Draw(fallback).ellipse((0, 0, size - 1, size - 1), fill=(70, 70, 78, 255))
    if not url:
        return _circle_crop(fallback, size)
    try:
        resp = requests.get(url, timeout=6)
        if resp.status_code != 200:
            return _circle_crop(fallback, size)
        raw = Image.open(io.BytesIO(resp.content))
        return _circle_crop(raw, size)
    except Exception:
        return _circle_crop(fallback, size)


def _get_player_avatar(player: dict, size: int) -> Image.Image:
    """Avatar z dziennego cache; fallback: URL / placeholder."""
    key = player.get("tracked_nickname") or player.get("nickname") or ""
    path = _avatar_cache_path(key) if key else ""
    if path and os.path.isfile(path):
        try:
            return _circle_crop(Image.open(path), size)
        except Exception:
            pass
    return _fetch_avatar(player.get("avatar"), size)


def _load_level_icon(level: int, size: int) -> Image.Image:
    path = os.path.join(FC_LVL_DIR, f"{int(level)}.png") if 1 <= int(level) <= 10 else None
    if path and os.path.isfile(path):
        try:
            return Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except Exception:
            pass
    placeholder = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(placeholder)
    draw.ellipse((1, 1, size - 2, size - 2), fill=(50, 50, 56, 255), outline=COLOR_MUTED)
    return placeholder


def _load_faceit_logo(size: int) -> Optional[Image.Image]:
    if not os.path.isfile(FACEIT_LOGO_PATH):
        return None
    try:
        return Image.open(FACEIT_LOGO_PATH).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return None


def _rank_color(place: int) -> tuple:
    if place == 1:
        return COLOR_GOLD
    if place == 2:
        return COLOR_SILVER
    if place == 3:
        return COLOR_BRONZE
    return COLOR_MUTED


def _format_delta(delta: int) -> Tuple[str, tuple]:
    if delta > 0:
        return f"+{delta}", COLOR_WIN
    if delta < 0:
        return str(delta), COLOR_LOSS
    return "0", COLOR_MUTED


def _form_dots_width(count: int, scale: int) -> int:
    r = 18 * scale
    gap = 10 * scale
    n = max(0, min(5, count))
    if n == 0:
        return 0
    return n * (2 * r) + (n - 1) * gap


def _draw_form_dots(draw: ImageDraw.ImageDraw, x: int, y: int, outcomes: List[str], scale: int) -> int:
    """Draw form dots; returns total width used."""
    r = 18 * scale
    gap = 10 * scale
    for i, outcome in enumerate(outcomes[:5]):
        cx = x + i * (2 * r + gap) + r
        if outcome == "W":
            fill = COLOR_WIN
        elif outcome == "L":
            fill = COLOR_LOSS
        else:
            fill = COLOR_NEUTRAL
        draw.ellipse((cx - r, y - r, cx + r, y + r), fill=fill)
    return _form_dots_width(len(outcomes[:5]), scale)


def _load_streak_icon(kind: str, size: int) -> Optional[Image.Image]:
    path = FIRE_EMOJI_PATH if kind == "win" else CRY_EMOJI_PATH
    if not os.path.isfile(path):
        return None
    try:
        return Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    except Exception:
        return None


def build_faceit_live_image(player_stats: Optional[List[dict]] = None) -> io.BytesIO:
    """Render FACEIT LIVE ranking as a modern dark PNG."""
    import faceit_utils as fu

    if player_stats is None:
        player_stats = collect_discordfaceit_player_stats()

    refresh_avatar_cache_if_needed(player_stats)

    daily_stats = fu.load_daily_stats()
    current_date = datetime.now().strftime("%Y-%m-%d")
    daily_map: Dict[str, int] = {}
    if daily_stats.get("date") == current_date:
        for key, value in (daily_stats.get("stats") or {}).items():
            daily_map[str(key).lower()] = int(value)

    rows = player_stats
    scale = 2
    pad = 28 * scale
    header_h = 76 * scale
    col_header_h = 48 * scale
    row_h = 100 * scale
    row_gap = 10 * scale
    panel_pad = 16 * scale
    radius = 16 * scale
    # Ikony prawie do krawędzi wiersza (~8px luzu z każdej strony)
    avatar_size = 84 * scale
    level_size = 84 * scale
    logo_size = 48 * scale
    streak_size = 56 * scale

    # Szeroki layout — nie zwężamy pod Discorda
    width = 1400 * scale
    footer_h = 52 * scale
    rows_h = max(1, len(rows)) * row_h + max(0, len(rows) - 1) * row_gap
    height = pad + header_h + col_header_h + rows_h + panel_pad * 2 + footer_h + pad

    img = Image.new("RGBA", (width, height), COLOR_BG)
    draw = ImageDraw.Draw(img)

    font_title = _load_font(44 * scale, weight="bold")
    font_col = _load_font(24 * scale, weight="semibold")
    font_rank = _load_font(36 * scale, weight="bold")
    font_nick = _load_font(42 * scale, weight="semibold")
    font_elo = _load_font(42 * scale, weight="bold")
    font_delta = _load_font(40 * scale, weight="semibold")
    font_footer = _load_font(28 * scale, weight="semibold")

    # Header — tylko logo + tytuł
    logo = _load_faceit_logo(logo_size)
    title = "FACEIT LIVE"
    title_x = pad
    if logo:
        logo_y = pad + (header_h - logo_size) // 2
        img.paste(logo, (pad, logo_y), logo)
        title_x = pad + logo_size + 14 * scale
    draw.text((title_x, pad + header_h // 2), title, fill=COLOR_TEXT, font=font_title, anchor="lm")

    line_y = pad + header_h - 6 * scale
    draw.rectangle((pad, line_y, width - pad, line_y + 4 * scale), fill=COLOR_ORANGE)

    panel_top = pad + header_h
    panel_bottom = pad + header_h + col_header_h + rows_h + panel_pad * 2
    _draw_rounded_rect(draw, (pad, panel_top, width - pad, panel_bottom), radius=radius, fill=COLOR_PANEL)

    content_left = pad + panel_pad
    content_right = width - pad - panel_pad
    col_y = panel_top + 12 * scale

    # Kolumny — pozycje treści
    x_rank = content_left + 12 * scale
    x_player = content_left + 70 * scale
    x_level = content_left + 500 * scale
    x_elo = content_left + 640 * scale
    x_today = content_left + 820 * scale
    x_form = content_left + 980 * scale

    # Nagłówki wyrównane do treści
    rank_center_x = x_rank + 10 * scale
    level_center_x = x_level + level_size // 2
    form_center_x = x_form + _form_dots_width(5, scale) // 2
    # ELO/DZIŚ — środek typowej wartości w kolumnie
    elo_center_x = x_elo + int(draw.textlength("0000", font=font_elo) / 2)
    today_center_x = x_today + int(draw.textlength("+00", font=font_delta) / 2)

    draw.text((rank_center_x, col_y), "#", fill=COLOR_MUTED, font=font_col, anchor="ma")
    draw.text((x_player, col_y), "GRACZ", fill=COLOR_MUTED, font=font_col, anchor="la")
    draw.text((level_center_x, col_y), "LVL", fill=COLOR_MUTED, font=font_col, anchor="ma")
    draw.text((elo_center_x, col_y), "ELO", fill=COLOR_MUTED, font=font_col, anchor="ma")
    draw.text((today_center_x, col_y), "DZIŚ", fill=COLOR_MUTED, font=font_col, anchor="ma")
    draw.text((form_center_x, col_y), "FORMA", fill=COLOR_MUTED, font=font_col, anchor="ma")

    rows_top = panel_top + col_header_h
    nick_max_w = x_level - x_player - avatar_size - 22 * scale

    fire_icon = _load_streak_icon("win", streak_size)
    cry_icon = _load_streak_icon("loss", streak_size)

    for idx, player in enumerate(rows):
        place = idx + 1
        row_y = rows_top + idx * (row_h + row_gap)
        row_box = (content_left, row_y, content_right, row_y + row_h)
        fill = COLOR_ROW if idx % 2 == 0 else COLOR_ROW_ALT
        _draw_rounded_rect(draw, row_box, radius=12 * scale, fill=fill)

        rank_color = _rank_color(place)
        rank_label = str(place)
        left, top, right, bottom = draw.textbbox((0, 0), rank_label, font=font_rank)
        draw.text(
            (x_rank + 10 * scale - (left + right) // 2, row_y + row_h // 2 - (top + bottom) // 2),
            rank_label,
            fill=rank_color,
            font=font_rank,
        )

        avatar = _get_player_avatar(player, avatar_size)
        avatar_x = x_player
        avatar_y = row_y + (row_h - avatar_size) // 2
        img.paste(avatar, (avatar_x, avatar_y), avatar)

        nick = player.get("nickname") or "?"
        while nick and draw.textlength(nick, font=font_nick) > nick_max_w and len(nick) > 3:
            nick = nick[:-2] + "…"
        nick_x = avatar_x + avatar_size + 14 * scale
        nick_color = rank_color if place <= 3 else COLOR_TEXT
        draw.text((nick_x, row_y + row_h // 2), nick, fill=nick_color, font=font_nick, anchor="lm")

        level = int(player.get("level") or 0)
        level_icon = _load_level_icon(level, level_size)
        level_y = row_y + (row_h - level_size) // 2
        img.paste(level_icon, (x_level, level_y), level_icon)

        elo_str = str(player.get("elo") or 0)
        draw.text((x_elo, row_y + row_h // 2), elo_str, fill=COLOR_TEXT, font=font_elo, anchor="lm")

        start = daily_map.get(str(player.get("tracked_nickname") or "").lower())
        if start is None:
            start = daily_map.get(str(player.get("nickname") or "").lower())
        delta = (int(player["elo"]) - start) if start is not None and isinstance(player.get("elo"), int) else 0
        delta_str, delta_color = _format_delta(delta)
        draw.text((x_today, row_y + row_h // 2), delta_str, fill=delta_color, font=font_delta, anchor="lm")

        outcomes = player.get("outcomes") or []
        form_w = _draw_form_dots(draw, x_form, row_y + row_h // 2, outcomes, scale)

        streak = player.get("streak_emoji") or ""
        if streak:
            icon = fire_icon if "🔥" in streak else cry_icon
            if icon is not None:
                # Przy prawej krawędzi wiersza — oddalone od kropek formy
                ix = content_right - streak_size - 14 * scale
                iy = row_y + (row_h - streak_size) // 2
                min_ix = x_form + form_w + 16 * scale
                if ix < min_ix:
                    ix = min_ix
                if ix + streak_size <= content_right - 4 * scale:
                    img.paste(icon, (ix, iy), icon)

    footer_now = (datetime.now() + timedelta(hours=2)).strftime("%H:%M:%S")
    footer_text = f"Odświeżanie co 5 min • {footer_now}"
    footer_y = panel_bottom + footer_h // 2
    draw.text((width // 2, footer_y), footer_text, fill=COLOR_MUTED, font=font_footer, anchor="mm")

    if scale > 1:
        img = img.resize((width // scale, height // scale), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer




def _is_live_image_attachment(attachment) -> bool:
    name = (getattr(attachment, "filename", None) or "").lower()
    return name == "faceit_live.png" or name.endswith("faceit_live.png")


def _parse_message_id(raw) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


async def _find_live_image_messages(channel, limit: int = 20) -> List[discord.Message]:
    """Zwraca wiadomości bota z grafiką live z ostatnich N wiadomości (najnowsze pierwsze)."""
    bot_user = getattr(CLIENT_REF, "user", None)
    if bot_user is None:
        return []

    found: List[discord.Message] = []
    try:
        async for msg in channel.history(limit=limit):
            if msg.author.id != bot_user.id:
                continue
            if any(_is_live_image_attachment(att) for att in msg.attachments):
                found.append(msg)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"Faceit live image: nie udało się przeskanować kanału {channel.id}: {exc}")
    return found


async def _resolve_live_image_message(channel, message_id: Optional[int]) -> Optional[discord.Message]:
    """Znajduje wiadomość do edycji: zapisane ID, potem skan ostatnich 20. Nie usuwa grafiki."""
    if message_id is not None:
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            print(
                f"Faceit live image: brak wiadomości {message_id} (fetch) — "
                f"szukam faceit_live.png w ostatnich 20 na kanale {channel.id}."
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"Faceit live image: nie udało się pobrać wiadomości {message_id}: {exc}")
            return None

    found = await _find_live_image_messages(channel, limit=20)
    if not found:
        return None

    # Najnowsza = docelowa; starsze duplikaty sprzątamy (bez ruszania tej do edycji).
    newest = found[0]
    for old in found[1:]:
        try:
            await old.delete()
            print(f"Faceit live image: usunięto duplikat {old.id}")
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass
    print(f"Faceit live image: używam istniejącej wiadomości {newest.id}")
    return newest


def _file_from_buffer(buffer: io.BytesIO) -> discord.File:
    raw = buffer.getvalue() if hasattr(buffer, "getvalue") else buffer.read()
    return discord.File(fp=io.BytesIO(raw), filename="faceit_live.png")


def _save_live_image_message(channel, message: discord.Message) -> None:
    # message_id jako string — snowflake > 2^53, bezpieczniej niż float/JSON number
    save_faceit_live_image_state(
        {"channel_id": channel.id, "message_id": str(message.id)}
    )


async def _edit_live_image(channel, buffer, message: discord.Message) -> discord.Message:
    await message.edit(attachments=[_file_from_buffer(buffer)])
    _save_live_image_message(channel, message)
    return message


async def _send_live_image(channel, buffer) -> discord.Message:
    message = await channel.send(file=_file_from_buffer(buffer))
    _save_live_image_message(channel, message)
    print(f"Faceit live image: utworzono wiadomość {message.id} na kanale {channel.id}")
    return message


async def _remove_previous_channel_message(state, current_channel_id):
    previous_channel_id = _parse_message_id(state.get("channel_id"))
    message_id = _parse_message_id(state.get("message_id"))
    if not previous_channel_id or previous_channel_id == current_channel_id or not message_id:
        return
    try:
        channel = CLIENT_REF.get_channel(previous_channel_id)
        if channel is None:
            channel = await CLIENT_REF.fetch_channel(previous_channel_id)
        message = await channel.fetch_message(message_id)
        if (message.author.id == CLIENT_REF.user.id
                and any(_is_live_image_attachment(attachment) for attachment in message.attachments)):
            await message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        print(f"Faceit live image: nie udało się usunąć poprzedniej wiadomości: {exc}")


async def refresh_faceit_live_image_message():
    """Odświeża grafikę FACEIT LIVE — edycja jednej wiadomości co 5 min."""
    import asyncio

    global _live_image_lock
    if _live_image_lock is None:
        _live_image_lock = asyncio.Lock()

    async with _live_image_lock:
        if not CLIENT_REF or not CLIENT_REF.is_ready():
            return

        config = load_config()
        channel_id = config["channel_id"]
        channel = CLIENT_REF.get_channel(channel_id)
        if channel is None:
            try:
                channel = await CLIENT_REF.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return

        if channel is None or not hasattr(channel, "send"):
            return

        try:
            buffer = await asyncio.to_thread(build_faceit_live_image)
        except Exception as exc:
            print(f"Faceit live image: błąd generowania grafiki: {exc}")
            return

        if load_config() != config:
            # Settings changed while the image was being generated; retry next cycle.
            return

        # Ponownie wczytaj stan po generowaniu (inny task mógł już zapisać message_id)
        state = load_faceit_live_image_state()
        message_id = (_parse_message_id(state.get("message_id"))
                      if str(state.get("channel_id")) == str(channel_id) else None)

        try:
            message = await _resolve_live_image_message(channel, message_id)
            if message is not None:
                await _edit_live_image(channel, buffer, message)
            else:
                print(
                    f"Faceit live image: brak grafiki w ostatnich 20 na kanale {channel.id} "
                    f"— wysyłam nową."
                )
                await _send_live_image(channel, buffer)
            await _remove_previous_channel_message(state, channel_id)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"Faceit live image: nie udało się zaktualizować wiadomości: {exc}")
        except Exception as exc:
            print(f"Faceit live image: nieoczekiwany błąd odświeżenia: {exc}")




@tasks.loop(minutes=5)
async def track_faceit_live_image():
    await refresh_faceit_live_image_message()


@track_faceit_live_image.before_loop
async def _before_faceit_live_image():
    if CLIENT_REF is not None:
        await CLIENT_REF.wait_until_ready()


async def start_faceit_live_tracking(client):
    global CLIENT_REF
    CLIENT_REF = client


    # Tylko pętla grafiki — odpala pierwsze odświeżenie sama.
    # Ręczne + start() powodowało race i dwa zdjęcia przy bootcie.
    if not track_faceit_live_image.is_running():
        track_faceit_live_image.start()
