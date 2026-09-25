import discord
from discord import app_commands
from discord.ext import tasks
import logging
import aiohttp
import asyncio
import json
import os
import re
import urllib.parse
from html.parser import HTMLParser
from html import unescape
from datetime import datetime, timedelta

STEAM_HISTORY_FILE = "txt/steam_history.json"
CS2_UPDATES_TRACKING_FILE = "txt/cs2_updates_tracking.json"
CS2_OFFICIAL_UPDATES_TRACKING_FILE = "txt/cs2_official_updates_tracking.json"
CS2_UPDATES_CHANNEL_ID = 1301248598108798996

def load_steam_history():
    if os.path.exists(STEAM_HISTORY_FILE):
        try:
            with open(STEAM_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading steam history: {e}")
    return {}

def save_steam_history(data):
    os.makedirs(os.path.dirname(STEAM_HISTORY_FILE), exist_ok=True)
    try:
        with open(STEAM_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving steam history: {e}")

def load_cs2_updates_tracking():
    """Ładuje dane śledzenia ostatniego commita CS2"""
    if os.path.exists(CS2_UPDATES_TRACKING_FILE):
        try:
            with open(CS2_UPDATES_TRACKING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading CS2 tracking data: {e}")
    return {"last_commit_sha": None}

def save_cs2_updates_tracking(data):
    """Zapisuje dane śledzenia ostatniego commita CS2"""
    os.makedirs(os.path.dirname(CS2_UPDATES_TRACKING_FILE), exist_ok=True)
    try:
        with open(CS2_UPDATES_TRACKING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving CS2 tracking data: {e}")

def load_cs2_official_tracking():
    if os.path.exists(CS2_OFFICIAL_UPDATES_TRACKING_FILE):
        try:
            with open(CS2_OFFICIAL_UPDATES_TRACKING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading official CS2 tracking data: {e}")
    return {"last_gid": None, "pending_news": []}

def save_cs2_official_tracking(data):
    os.makedirs(os.path.dirname(CS2_OFFICIAL_UPDATES_TRACKING_FILE), exist_ok=True)
    try:
        with open(CS2_OFFICIAL_UPDATES_TRACKING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error saving official CS2 tracking data: {e}")

class _SteamNewsText(HTMLParser):
    BLOCK_TAGS = {"br", "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "tr"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

def steam_news_plain_text(content: str) -> str:
    """Converts Steam HTML/BBCode news into compact Discord markdown.

    Steam's official announcements are usually BBCode rather than HTML.  The
    old parser only understood HTML block tags, which left strings such as
    ``[p]`` and ``[/list]`` visible in Discord.
    """
    if not content:
        return ""

    # Turn the most useful Steam BBCode into markdown before parsing any HTML.
    # The URL form is deliberately kept as a markdown link so the source link
    # remains clickable after the announcement is shortened for Discord.
    content = re.sub(
        r"\[url=(?:\"|')?([^\"'\]]+)(?:\"|')?\](.*?)\[/url\]",
        r"[\2](\1)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    content = re.sub(r"\[url\](.*?)\[/url\]", r"\1", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"\[/?(?:img|video|table|tr|td)[^\]]*\]", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\[/?(?:p|div|h[1-6])\]", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"\[list[^\]]*\]", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"\[/list\]", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"\[\*\]", "\n• ", content, flags=re.IGNORECASE)
    content = re.sub(r"\[/?(?:br)\]", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"\[b\](.*?)\[/b\]", r"**\1**", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"\[i\](.*?)\[/i\]", r"*\1*", content, flags=re.IGNORECASE | re.DOTALL)

    parser = _SteamNewsText()
    try:
        parser.feed(content or "")
        text = unescape("".join(parser.parts))
    except Exception:
        text = unescape(content or "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    # Make the category labels from update notes stand out instead of leaving
    # a very long, visually uniform paragraph.  Example: ``MAPS | ...``.
    formatted = []
    for line in lines:
        match = re.match(r"^(?:•\s*)?([A-Z][A-Z0-9 /_-]{2,36})\s*\|\s*(.+)$", line)
        if match:
            prefix = "• " if line.startswith("•") else ""
            line = f"{prefix}**{match.group(1).strip()}** — {match.group(2).strip()}"
        formatted.append(line)
    return "\n".join(formatted)


def _limit_discord_description(text: str, limit: int = 3900) -> str:
    """Keep room for a short footer/link while ending at a complete line."""
    if len(text) <= limit:
        return text
    shortened = text[: limit - 18].rsplit("\n", 1)[0].rstrip()
    return f"{shortened}\n… [pełna notka w Steamie]"


def build_official_cs2_embed_legacy(items: list) -> discord.Embed:
    """Build one readable embed for one or more queued official announcements."""
    if not items:
        raise ValueError("At least one official CS2 announcement is required")

    sections = []
    for index, item in enumerate(items):
        title = (item.get("title") or "Oficjalna aktualizacja CS2").strip()
        body = steam_news_plain_text(item.get("contents", ""))
        if not body:
            body = "Otwórz link, aby zobaczyć pełną notkę."
        if len(items) > 1:
            sections.append(f"**{title}**\n{body}")
        else:
            sections.append(body)

    latest = items[-1]
    embed = discord.Embed(
        title="📢 Counter-Strike 2 — oficjalne zmiany",
        description=_limit_discord_description("\n\n".join(sections)),
        color=discord.Color.from_rgb(210, 55, 45),
        url=latest.get("url") or "https://store.steampowered.com/news/app/730",
    )
    date = latest.get("date")
    if date:
        embed.timestamp = datetime.fromtimestamp(date)
    footer = "Oficjalne ogłoszenie Steam | Counter-Strike 2"
    if len(items) > 1:
        footer += f" | {len(items)} wpisy połączone w jeden update"
    embed.set_footer(text=footer)
    return embed

def extract_steam_news_images(content: str) -> list[str]:
    """Return image URLs embedded in Steam BBCode or HTML."""
    if not content:
        return []

    patterns = (
        r"\[img\]\s*(https?://[^\s\[]+)\s*\[/img\]",
        r"<img[^>]+src=[\"'](https?://[^\"']+)[\"']",
    )
    images = []
    for pattern in patterns:
        images.extend(re.findall(pattern, content, flags=re.IGNORECASE))

    return list(dict.fromkeys(images))


def steam_news_sections(content: str) -> list[tuple[str, list[str]]]:
    """Parse Steam's nested BBCode into heading + readable bullet lines."""
    if not content:
        return []

    content = unescape(content).replace("\xa0", " ")
    # Steam escapes literal section brackets as ``\\[ RUSH ]``.
    content = content.replace(r"\[", "[")

    # Remove media after extracting it, then normalize Steam's list syntax.
    content = re.sub(r"\[img\].*?\[/img\]", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"\[video\].*?\[/video\]", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<img[^>]*>", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\[/\*+\]|\[/\]", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\[\*\]\s*", "\n- ", content, flags=re.IGNORECASE)
    content = re.sub(r"\[/?(?:p|div|list|br|table|tr|td)[^\]]*\]", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"\[b\](.*?)\[/b\]", r"**\1**", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"\[i\](.*?)\[/i\]", r"*\1*", content, flags=re.IGNORECASE | re.DOTALL)
    # Any remaining BBCode is presentation noise, not user-facing content.
    content = re.sub(
        r"\[(?!\s*[A-Z0-9][A-Z0-9 /_-]{1,32}\s*\])[^\]]+\]",
        "",
        content,
    )

    parser = _SteamNewsText()
    try:
        parser.feed(content)
        text = unescape("".join(parser.parts))
    except Exception:
        text = content

    sections: list[tuple[str, list[str]]] = []
    heading = "Zmiany"
    lines: list[str] = []
    heading_pattern = re.compile(r"^\[\s*([A-Z0-9][A-Z0-9 /_-]{1,32})\s*\]$")
    plain_heading_pattern = re.compile(r"^[A-Z][A-Z0-9 /_-]{2,32}$")

    def flush() -> None:
        nonlocal lines
        cleaned = []
        for line in lines:
            line = re.sub(r"[ \t]+", " ", line).strip()
            line = line.strip("[]") if re.fullmatch(r"[\[\]/ ]+", line) else line
            if line and line not in {"-", "[/]", "[/*]"}:
                cleaned.append(line if line.startswith("- ") else f"- {line}")
        if cleaned:
            sections.append((heading, cleaned))
        lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = heading_pattern.match(line)
        if match:
            flush()
            heading = match.group(1).strip().title()
            continue
        if plain_heading_pattern.match(line) and not line.startswith("-"):
            flush()
            heading = line.title()
            continue
        if line:
            lines.append(line)
    flush()
    return sections


def _chunk_embed_lines(lines: list[str], limit: int = 1000) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        addition = len(line) + (1 if current else 0)
        if current and length + addition > limit:
            chunks.append("\n".join(current))
            current = []
            length = 0
        current.append(line)
        length += len(line) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks or ["Brak opisanych zmian."]


def build_official_cs2_embed(items: list) -> discord.Embed:
    """Build a field-based, image-enabled embed for official CS2 notes."""
    if not items:
        raise ValueError("At least one official CS2 announcement is required")

    latest = items[-1]
    source_url = latest.get("url") or "https://store.steampowered.com/news/app/730"
    embed = discord.Embed(
        title="📢 Counter-Strike 2 — oficjalne zmiany",
        description=(
            "Najnowsza notka aktualizacji z oficjalnego kanału Steam.\n"
            f"[Otwórz pełną notkę na Steam]({source_url})"
        ),
        color=discord.Color.from_rgb(210, 55, 45),
        url=source_url,
    )

    all_images = []
    field_count = 0
    for item in items:
        if len(items) > 1:
            title = (item.get("title") or "Oficjalna aktualizacja CS2").strip()
            embed.add_field(name=f"> {title}", value="\u200b", inline=False)
            field_count += 1
        all_images.extend(extract_steam_news_images(item.get("contents", "")))
        for section_name, lines in steam_news_sections(item.get("contents", "")):
            for index, chunk in enumerate(_chunk_embed_lines(lines), start=1):
                suffix = f" ({index})" if len(_chunk_embed_lines(lines)) > 1 else ""
                embed.add_field(name=f"{section_name}{suffix}", value=chunk, inline=False)
                field_count += 1
                if field_count >= 23:
                    embed.add_field(name="Więcej zmian", value=f"[Zobacz pełną notkę na Steam]({source_url})", inline=False)
                    field_count += 1
                    break
            if field_count >= 23:
                break
        if field_count >= 23:
            break

    # Steam often omits the image from the News API payload.  The official
    # CS2 header is a safe visual fallback, while real article images win.
    image_url = all_images[0] if all_images else "https://cdn.cloudflare.steamstatic.com/steam/apps/730/header.jpg"
    embed.set_thumbnail(url=image_url)
    date = latest.get("date")
    if date:
        embed.timestamp = datetime.fromtimestamp(date)
    embed.set_footer(text="Źródło: Steam Community Announcements | Counter-Strike 2")
    return embed


def build_official_cs2_view(items: list) -> discord.ui.LayoutView:
    """Build a Components V2 changelog layout.

    LayoutView replaces the old single giant embed with real text blocks,
    separators, a thumbnail accessory and an optional media gallery.
    """
    if not items:
        raise ValueError("At least one official CS2 announcement is required")

    latest = items[-1]
    source_url = latest.get("url") or "https://store.steampowered.com/news/app/730"
    image_urls = []
    for item in items:
        image_urls.extend(extract_steam_news_images(item.get("contents", "")))
    image_urls = list(dict.fromkeys(image_urls))
    fallback_image = "https://cdn.cloudflare.steamstatic.com/steam/apps/730/header.jpg"
    thumbnail_url = image_urls[0] if image_urls else fallback_image

    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=0xD2372D)
    header = discord.ui.Section(
        discord.ui.TextDisplay("# 📢 Counter-Strike 2"),
        discord.ui.TextDisplay(
            "## Oficjalne zmiany\n"
            f"-# [Otwórz pełną notkę na Steam]({source_url})"
        ),
        accessory=discord.ui.Thumbnail(
            thumbnail_url,
            description="Counter-Strike 2 update",
        ),
    )
    container.add_item(header)
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    for item_index, item in enumerate(items):
        item_title = (item.get("title") or "Oficjalna aktualizacja CS2").strip()
        if len(items) > 1:
            container.add_item(discord.ui.TextDisplay(f"## {item_title}"))

        sections = steam_news_sections(item.get("contents", ""))
        if not sections:
            sections = [("Zmiany", ["- Brak opisanych zmian."])]

        for section_index, (section_name, lines) in enumerate(sections):
            chunks = _chunk_embed_lines(lines, limit=3800)
            for chunk_index, chunk in enumerate(chunks):
                suffix = f" ({chunk_index + 1})" if len(chunks) > 1 else ""
                container.add_item(discord.ui.TextDisplay(f"## {section_name}{suffix}\n{chunk}"))
            if section_index < len(sections) - 1:
                container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        if item_index < len(items) - 1:
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    # Real article images are shown in a gallery. The thumbnail above remains
    # useful for posts that have no media in the Steam News API payload.
    if len(image_urls) > 1:
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        container.add_item(
            discord.ui.MediaGallery(
                *[
                    discord.MediaGalleryItem(url, description="Obraz z oficjalnej notki Steam")
                    for url in image_urls[:10]
                ]
            )
        )

    date = latest.get("date")
    date_text = f"<t:{int(date)}:f>" if date else "data nieznana"
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(
        discord.ui.TextDisplay(
            f"-# Źródło: [Steam Community Announcements]({source_url}) | opublikowano: {date_text}"
        )
    )
    view.add_item(container)
    return view


async def fetch_official_cs2_news(session: aiohttp.ClientSession, count: int = 10):
    """Pobiera oficjalne wpisy CS2 z publicznego Steam News API."""
    url = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    params = {"appid": 730, "count": count, "maxlength": 0, "feeds": "steam_community_announcements"}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("appnews", {}).get("newsitems", [])
            logging.error(f"Steam News API error: {resp.status}")
    except Exception as e:
        logging.error(f"Error fetching official CS2 news: {e}")
    return []

@tasks.loop(minutes=15)
async def monitor_official_cs2_updates_loop():
    from startup_guard import allow_background_api

    if not allow_background_api("monitor oficjalnych aktualizacji CS2"):
        return
    try:
        async with aiohttp.ClientSession() as session:
            news = await fetch_official_cs2_news(session)
        if not news:
            return

        tracking = load_cs2_official_tracking()
        last_gid = str(tracking.get("last_gid")) if tracking.get("last_gid") else None
        latest_gid = str(news[0].get("gid", ""))

        # Przy pierwszym uruchomieniu zapamiętaj aktualny wpis bez wysyłania archiwum.
        if last_gid is None:
            tracking["last_gid"] = latest_gid
            tracking["pending_news"] = []
            save_cs2_official_tracking(tracking)
            logging.info("Initialized official CS2 news tracking")
            return

        pending = tracking.get("pending_news", [])
        if latest_gid != last_gid:
            tracking["last_gid"] = latest_gid
            # Steam returns newest first. Keep only the current latest entry;
            # intermediate announcements are intentionally skipped.
            tracking["pending_news"] = [news[0]]
            save_cs2_official_tracking(tracking)
            logging.info("Queued the latest official CS2 announcement")
        elif pending:
            # Also compact backlogs created by older versions of this loop.
            tracking["pending_news"] = [news[0]]
            save_cs2_official_tracking(tracking)
            logging.info(f"Discarded stale official CS2 backlog ({len(pending)} queued item(s))")
    except Exception as e:
        logging.error(f"Error in official CS2 updates loop: {e}")

async def fetch_case_history(session: aiohttp.ClientSession, case_name: str):
    """Pobiera stronę HTML skrzynki i wyciąga historię cen"""
    # Kodowanie URL, aby poprawnie odczytać znak spacji i inne np. '&'
    safe_name = urllib.parse.quote(case_name)
    url = f"https://steamcommunity.com/market/listings/730/{safe_name}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                html = await resp.text()
                match = re.search(r'var line1=([^;]+);', html)
                if match:
                    history_data = json.loads(match.group(1))
                    return history_data
    except Exception as e:
        logging.error(f"Error fetching HTML for {case_name}: {e}")
    return None

def process_history(history_list):
    """Przetwarza surową listę ze Steama na konkretne punkty w czasie"""
    if not history_list:
        return None
        
    now = datetime.utcnow()
    parsed_data = []
    
    # Format to zazwyczaj: ["Apr 08 2025 01: +0", 2.741, "235981"]
    for item in history_list:
        try:
            date_str = item[0][:11] # Pobieramy m.in. "Apr 08 2025"
            date_obj = datetime.strptime(date_str, "%b %d %Y")
            price = float(item[1])
            parsed_data.append((date_obj, price))
        except:
            continue
            
    if not parsed_data:
        return None
        
    current_price = parsed_data[-1][1]
    
    def get_price_at_offset(days_offset):
        target_date = now - timedelta(days=days_offset)
        closest_price = current_price
        min_diff = float('inf')
        for dt, pr in parsed_data:
            diff = abs((dt - target_date).total_seconds())
            if diff < min_diff:
                min_diff = diff
                closest_price = pr
        return closest_price
        
    return {
        "current": current_price,
        "1D": get_price_at_offset(1),
        "7D": get_price_at_offset(7),
        "30D": get_price_at_offset(30),
        "365D": get_price_at_offset(365)
    }

def format_price_diff(current, old):
    diff = current - old
    if diff > 0:
        return f"📈 +${diff:.2f}"
    elif diff < 0:
        return f"📉 -${abs(diff):.2f}"
    return "➖ b/z"

async def fetch_cs2_commits(session: aiohttp.ClientSession, per_page: int = 3):
    """Pobiera ostatnie commity z repozytorium GameTracking-CS2"""
    url = "https://api.github.com/repos/SteamDatabase/GameTracking-CS2/commits"
    params = {
        "per_page": per_page,
        "page": 1
    }
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Bot-Geekot"
    }
    
    try:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                logging.error(f"GitHub API error: {resp.status}")
    except Exception as e:
        logging.error(f"Error fetching CS2 commits: {e}")
    return []

async def fetch_commit_details(session: aiohttp.ClientSession, commit_sha: str):
    """Pobiera szczegóły konkretnego commita"""
    url = f"https://api.github.com/repos/SteamDatabase/GameTracking-CS2/commits/{commit_sha}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Bot-Geekot"
    }
    
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logging.error(f"Error fetching commit details: {e}")
    return None

def format_file_changes(files: list) -> str:
    """Formatuje zmiany w plikach na czytelny format - max 1024 znaki"""
    if not files:
        return "Brak informacji o zmianach"
    
    # Filtruj tylko zmienione pliki (modified, added, removed)
    important_files = [f for f in files if f.get("status") in ["modified", "added", "removed"]]
    
    # Jeśli brak ważnych zmian, pokaz sumę
    if not important_files:
        return f"Zmieniono {len(files)} plik(ów)"
    
    formatted = ""
    for file in important_files[:12]:  # Limit do 12 plików
        filename = file.get("filename", "Unknown")
        status = file.get("status", "unknown")
        additions = file.get("additions", 0)
        deletions = file.get("deletions", 0)
        
        # Emoji dla różnych typów zmian
        status_emoji = {
            "added": "✅",
            "removed": "❌",
            "modified": "📝"
        }.get(status, "❓")
        
        change_summary = ""
        if additions > 0:
            change_summary += f"+{additions}"
        if deletions > 0:
            if change_summary:
                change_summary += f"/-{deletions}"
            else:
                change_summary = f"-{deletions}"
        
        # Skracaj nazwy plików jeśli za długie
        if len(filename) > 50:
            filename = filename[:47] + "..."
        
        line = f"{status_emoji} `{filename}`"
        if change_summary:
            line += f" ({change_summary})"
        line += "\n"
        
        # Sprawdzaj czy nie przekroczymy limitu
        if len(formatted) + len(line) > 1000:
            remaining = len(important_files) - (important_files.index(file) + 1)
            if remaining > 0:
                formatted += f"\n... i {remaining} więcej plik(ów)"
            break
        
        formatted += line
    
    return formatted if formatted else f"Zmieniono {len(important_files)} plik(ów)"

@tasks.loop(minutes=15)
async def monitor_cs2_updates_loop():
    """Monitoruje aktualizacje CS2 z GameTracking-CS2"""
    from startup_guard import allow_background_api

    if not allow_background_api("monitor aktualizacji CS2"):
        return

    try:
        tracking_data = load_cs2_updates_tracking()
        last_commit_sha = tracking_data.get("last_commit_sha")
        
        async with aiohttp.ClientSession() as session:
            commits = await fetch_cs2_commits(session)
            
            if not commits:
                return
            
            latest_commit = commits[0]
            latest_sha = latest_commit.get("sha")
            
            if latest_sha != last_commit_sha:
                commit_details = await fetch_commit_details(session, latest_sha)
                
                if commit_details:
                    tracking_data["last_commit_sha"] = latest_sha
                    tracking_data["last_commit_time"] = datetime.now().isoformat()
                    tracking_data["pending_commits"] = tracking_data.get("pending_commits", [])
                    
                    tracking_data["pending_commits"].append(commit_details)
                    save_cs2_updates_tracking(tracking_data)
                    
                    logging.info(f"Nowa aktualizacja CS2: {commit_details.get('commit', {}).get('message', 'N/A').split(chr(10))[0]}")
    
    except Exception as e:
        logging.error(f"Error in CS2 updates loop: {e}")

@tasks.loop(hours=6)
async def update_steam_history_loop():
    logging.info("Rozpoczęto w tle odświeżanie statystyk skrzynek Steam...")
    
    url = "https://steamcommunity.com/market/search/render/"
    params = {
        "query": "Case",
        "search_descriptions": 0,
        "sort_column": "quantity",
        "sort_dir": "desc",
        "appid": 730,
        "category_730_Type[]": "tag_CSGO_Type_WeaponCase",
        "norender": 1,
        "currency": 6 # PLN
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with aiohttp.ClientSession() as session:
            all_results = []
            for start_offset in [0, 10, 20]:
                params["start"] = start_offset
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and data.get("success"):
                            all_results.extend(data.get("results", []))
                        else:
                            break
                    else:
                        break
                        
            cases = []
            if all_results:
                for item in all_results:
                    name_lower = item.get("name", "").lower()
                    if "case" in name_lower and "capsule" not in name_lower and "package" not in name_lower:
                        if not any(c.get("name") == item.get("name") for c in cases):
                            cases.append(item)
                    if len(cases) == 15:
                        break
                        
            if not cases:
                logging.error("Steam history worker failed to fetch top cases.")
                return
                
            history_cache = load_steam_history()
            history_cache["top_cases"] = cases 
            history_cache["history"] = history_cache.get("history", {})
            
            for item in cases:
                name = item.get("name")
                raw_history = await fetch_case_history(session, name)
                if raw_history:
                    processed = process_history(raw_history)
                    if processed:
                        processed["sell_price_text"] = item.get("sell_price_text", "?")
                        processed["sell_listings"] = item.get("sell_listings", 0)
                        history_cache["history"][name] = processed
                        
                # Usypiamy bota na moment, by nie łamać rate limtów Steama
                await asyncio.sleep(2)
                
            history_cache["last_updated"] = datetime.now().isoformat()
            save_steam_history(history_cache)
            logging.info("Zakończono zapisywane historii skrzynek Steam.")
            
    except Exception as e:
        logging.error(f"Error in steam history background task: {e}")

async def setup_steam_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id: int):
    guild_obj = discord.Object(id=guild_id)
    
    if not monitor_cs2_updates_loop.is_running():
        monitor_cs2_updates_loop.start()
    if not monitor_official_cs2_updates_loop.is_running():
        monitor_official_cs2_updates_loop.start()
    
    async def send_pending_cs2_updates():
        """Wysyła zakolejkowane aktualizacje CS2 na Discord"""
        from startup_guard import allow_background_api

        if not allow_background_api("posty z aktualizacjami CS2"):
            return

        tracking_data = load_cs2_updates_tracking()
        pending_commits = tracking_data.get("pending_commits", [])
        
        if not pending_commits:
            return
        
        try:
            channel = client.get_channel(CS2_UPDATES_CHANNEL_ID)
            if not channel:
                logging.error(f"Cannot find CS2 updates channel: {CS2_UPDATES_CHANNEL_ID}")
                return
            
            for commit in pending_commits[:5]:  # Wysyłaj max 5 na raz
                try:
                    commit_msg = commit.get("commit", {}).get("message", "N/A")
                    commit_sha = commit.get("sha", "?")[:7]
                    author = commit.get("commit", {}).get("author", {}).get("name", "Unknown")
                    files = commit.get("files", [])
                    
                    # Pierwsze 50 znaków wiadomości
                    title = commit_msg.split('\n')[0][:100]
                    
                    embed = discord.Embed(
                        title="🎮 CS2 Update",
                        description=f"**{title}**",
                        color=discord.Color.blue(),
                        url=f"https://github.com/SteamDatabase/GameTracking-CS2/commit/{commit.get('sha')}"
                    )
                    
                    embed.add_field(
                        name="Commit SHA",
                        value=f"`{commit_sha}`",
                        inline=True
                    )
                    
                    embed.add_field(
                        name="Autor",
                        value=author,
                        inline=True
                    )
                    
                    embed.add_field(
                        name="📁 Zmienione pliki",
                        value=format_file_changes(files),
                        inline=False
                    )
                    
                    # Dodaj linkę do fullmessage jeśli jest wieloliniowa
                    if '\n' in commit_msg:
                        embed.add_field(
                            name="📝 Full Commit",
                            value=f"[Zobacz pełną wiadomość](https://github.com/SteamDatabase/GameTracking-CS2/commit/{commit.get('sha')})",
                            inline=False
                        )
                    
                    embed.set_footer(text="GameTracking-CS2 | SteamDatabase")
                    
                    await channel.send(embed=embed)
                    await asyncio.sleep(1)  # Delay między wiadomościami
                    
                except Exception as e:
                    logging.error(f"Error sending CS2 update: {e}")
            
            # Wyczyść wysłane commity
            tracking_data["pending_commits"] = []
            save_cs2_updates_tracking(tracking_data)
            
        except Exception as e:
            logging.error(f"Error in send_pending_cs2_updates: {e}")
    
    # Dodaj task do wysyłania commitów
    @tasks.loop(seconds=30)
    async def cs2_update_sender():
        await send_pending_cs2_updates()
    
    if not cs2_update_sender.is_running():
        cs2_update_sender.start()

    async def send_official_cs2_item(item, channel):
        """Send one official Steam announcement as one Discord message."""
        if not item or not channel:
            return False
        try:
            await channel.send(view=build_official_cs2_view([item]))
            return True
        except Exception as e:
            logging.error(f"Error sending official CS2 announcement: {e}")
            return False

    def official_cs2_item_key(item: dict) -> str:
        """Stable cross-instance key for an official Steam announcement."""
        return str(item.get("url") or item.get("gid") or "").strip()

    async def find_posted_official_cs2_keys(channel, items: list[dict]) -> set[str]:
        """Search recent channel messages, including Components V2 payloads."""
        keys = {official_cs2_item_key(item) for item in items}
        keys.discard("")
        if not keys:
            return set()

        found = set()
        async for message in channel.history(limit=500):
            # Components V2 does not populate message.content. Serializing the
            # component objects also catches URLs inside TextDisplay/Section.
            component_payloads = []
            for component in getattr(message, "components", []):
                try:
                    component_payloads.append(repr(component.to_dict()))
                except AttributeError:
                    component_payloads.append(repr(component))
            embed_payloads = [
                getattr(embed, "url", "") or repr(embed)
                for embed in getattr(message, "embeds", [])
            ]
            searchable = "\n".join(
                (
                    message.content or "",
                    repr(component_payloads),
                    repr(embed_payloads),
                )
            )
            for key in keys:
                if key in searchable:
                    found.add(key)
            if found == keys:
                break
        return found

    async def send_pending_official_cs2_updates():
        from startup_guard import allow_background_api

        if not allow_background_api("oficjalne posty z aktualizacjami CS2"):
            return
        tracking = load_cs2_official_tracking()
        pending = tracking.get("pending_news", [])
        if not pending:
            return
        channel = client.get_channel(CS2_UPDATES_CHANNEL_ID)
        if not channel:
            logging.error(f"Cannot find CS2 updates channel: {CS2_UPDATES_CHANNEL_ID}")
            return

        # Resolve the current newest item from Steam instead of draining an
        # old backlog. This also repairs pending queues written by older code.
        try:
            async with aiohttp.ClientSession() as session:
                news = await fetch_official_cs2_news(session)
        except Exception as e:
            logging.error(f"Error refreshing latest official CS2 announcement: {e}")
            return
        if not news:
            return

        latest_item = news[0]
        latest_gid = str(latest_item.get("gid", ""))
        if not latest_gid:
            logging.error("Latest official CS2 announcement has no gid")
            return

        tracking["last_gid"] = latest_gid
        tracking["pending_news"] = [latest_item]
        save_cs2_official_tracking(tracking)
        latest_key = official_cs2_item_key(latest_item)

        try:
            posted_keys = await find_posted_official_cs2_keys(channel, [latest_item])
        except (discord.Forbidden, discord.HTTPException) as e:
            # Without history access, sending would risk duplicating a post
            # sent by another bot instance. Leave it queued for the next run.
            logging.error(f"Cannot verify existing CS2 announcements: {e}")
            return

        def retain_newer_pending_updates() -> None:
            current = load_cs2_official_tracking()
            latest_date = int(latest_item.get("date") or 0)
            candidates = [
                item
                for item in current.get("pending_news", [])
                if official_cs2_item_key(item) != latest_key
                and int(item.get("date") or 0) >= latest_date
            ]
            if candidates:
                newest = max(candidates, key=lambda item: int(item.get("date") or 0))
                current["pending_news"] = [newest]
            else:
                current["pending_news"] = []
            save_cs2_official_tracking(current)

        if latest_key in posted_keys:
            retain_newer_pending_updates()
            logging.info("Skipped the latest official CS2 announcement because it was already posted")
            return

        if await send_official_cs2_item(latest_item, channel):
            retain_newer_pending_updates()
        return

    @tasks.loop(seconds=30)
    async def official_cs2_update_sender():
        await send_pending_official_cs2_updates()

    if not official_cs2_update_sender.is_running():
        official_cs2_update_sender.start()

    async def case_autocomplete(interaction: discord.Interaction, current: str):
        history_cache = load_steam_history()
        top_cases = history_cache.get("top_cases", [])
        
        choices = []
        for case_item in top_cases:
            name = case_item.get("name", "")
            if current.lower() in name.lower():
                choices.append(app_commands.Choice(name=name, value=name))
                if len(choices) >= 25:
                    break
        return choices

    @tree.command(name="skrzynki", description="Wyświetla skrzynki na rynku Steam z historią zmian cen", guild=guild_obj)
    @app_commands.autocomplete(nazwa=case_autocomplete)
    @app_commands.describe(nazwa="Wybierz konkretną skrzynkę (opcjonalnie)")
    async def skrzynki(interaction: discord.Interaction, nazwa: str = None):
        await interaction.response.defer()
        
        history_cache = load_steam_history()
        top_cases = history_cache.get("top_cases", [])
        history = history_cache.get("history", {})

        if not top_cases or not history:
            # Jeśli bot się dopiero obudził i loop jeszcze nie skończył
            await interaction.followup.send("⏳ Bot pobiera właśnie pierwsze historyczne wykresy Steama w tle. Z racji limitów odpytań może to potrwać około 30 sekund. Spróbuj powtórzyć komendę za chwilę!")
            return

        if nazwa:
            if nazwa not in history:
                # Dociągnij na żywo z API (brakującą pozycję spoza top15)
                async with aiohttp.ClientSession() as session:
                    raw_history = await fetch_case_history(session, nazwa)
                    if raw_history:
                        processed = process_history(raw_history)
                        if processed:
                            history[nazwa] = processed
                            history[nazwa]["sell_price_text"] = f"${processed['current']:.2f}"
                            history[nazwa]["sell_listings"] = "Brak Info"
                        else:
                            await interaction.followup.send("Przepraszam, nie udało się przetworzyć danych dla tej skrzynki.")
                            return
                    else:
                        await interaction.followup.send("Nie odnaleziono takiej skrzynki, upewnij się że wpisujesz poprawną angielską nazwę.")
                        return

            case_data = history[nazwa]
            
            embed = discord.Embed(title=f"📦 Analiza: {nazwa}", color=discord.Color.gold())
            embed.description = (
                f"**Aktualna wycena:** {case_data.get('sell_price_text')} (Ilość: {case_data.get('sell_listings')})\n\n"
                f"**Historia Zmian:**\n"
                f"📅 **1 Dzień:** {format_price_diff(case_data['current'], case_data['1D'])} (Z ${case_data['1D']:.2f})\n"
                f"📅 **1 Tydzień:** {format_price_diff(case_data['current'], case_data['7D'])} (Z ${case_data['7D']:.2f})\n"
                f"📅 **1 Miesiąc:** {format_price_diff(case_data['current'], case_data['30D'])} (Z ${case_data['30D']:.2f})\n"
                f"📅 **1 Rok:** {format_price_diff(case_data['current'], case_data['365D'])} (Z ${case_data['365D']:.2f})\n"
            )
            embed.set_footer(text="Dane szacownicze pobrane na podstawie wykresu ze Steam Community Market.")
            await interaction.followup.send(embed=embed)
            return

        # Użytkownik chce zsumowaną listę 15
        embed = discord.Embed(title="📦 Najpopularniejsze Skrzynki na Rynku Steam", color=discord.Color.dark_theme())
            
        desc = ""
        for idx, item in enumerate(top_cases, 1):
            name = item.get("name", "Nieznana skrzynka")
            price = item.get("sell_price_text", "?")
            quantity = item.get("sell_listings", 0)
            if isinstance(quantity, int):
                quantity_str = f"{quantity:,}".replace(",", " ")
            else:
                quantity_str = str(quantity)
            
            hist_str = ""
            case_data = history.get(name)
            if case_data:
                d1 = case_data["current"] - case_data["1D"]
                d7 = case_data["current"] - case_data["7D"]
                d365 = case_data["current"] - case_data["365D"]
                
                def sf(d):
                    if d > 0: return f"+${d:.2f}"
                    elif d < 0: return f"-${abs(d):.2f}"
                    return "b/z"
                
                hist_str = f" | 1D: ({sf(d1)}) | 1W: ({sf(d7)}) | 1Y: ({sf(d365)})"

            desc += f"**{idx}.** {name}\n💰 Cena: **{price}**{hist_str}\n📦 Dostępne: **{quantity_str}**\n\n"
        
        embed.description = desc
        embed.set_footer(text="Dane pobrane ze Steam Community Market.")
        
        await interaction.followup.send(embed=embed)
