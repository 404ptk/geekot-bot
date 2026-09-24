from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import discord

from jobs.providers.isitfair import fetch_offers_page as isitfair_fetch_page
from jobs.providers.olx import fetch_offers_page as olx_fetch_page
from jobs.providers.sources import source_label

WARSAW_TZ = ZoneInfo("Europe/Warsaw")
API_STATUS_EMBED_TITLE = "STAN API"

MONITORED_SERVICES: List[Tuple[str, str]] = [
    ("justjoin.it", "isitfair"),
    ("nofluffjobs.com", "isitfair"),
    ("pracuj.pl", "isitfair"),
]


def probe_service(service_id: str, provider: str) -> Tuple[bool, Optional[str]]:
    if provider == "isitfair":
        offers = isitfair_fetch_page(
            {"offer_status": "active", "offer_source": service_id},
            page=1,
            raise_errors=True,
        )
    else:
        offers = olx_fetch_page(query=None, offset=0, raise_errors=True)
    if offers:
        return True, None
    return False, "pusta odpowiedź API"


def check_all_services() -> Tuple[Dict[str, bool], Dict[str, str]]:
    results: Dict[str, bool] = {}
    failures: Dict[str, str] = {}
    for service_id, provider in MONITORED_SERVICES:
        try:
            is_available, reason = probe_service(service_id, provider)
            results[service_id] = is_available
            if not is_available and reason:
                failures[service_id] = reason
        except Exception as exc:
            results[service_id] = False
            failures[service_id] = str(exc)
    return results, failures


def build_status_embed(results: Dict[str, bool], checked_at: datetime) -> discord.Embed:
    lines = []
    for service_id, _ in MONITORED_SERVICES:
        label = source_label(service_id)
        emoji = "🟢" if results.get(service_id) else "🔴"
        lines.append(f"{emoji} {label}")

    all_ok = all(results.get(service_id) for service_id, _ in MONITORED_SERVICES)
    local_time = checked_at.astimezone(WARSAW_TZ)
    tz_label = local_time.strftime("%Z")

    embed = discord.Embed(
        title=API_STATUS_EMBED_TITLE,
        description="\n".join(lines),
        color=discord.Color.green() if all_ok else discord.Color.orange(),
    )
    embed.set_footer(text=f"Ostatnia próba: {local_time.strftime('%H:%M')} {tz_label}")
    return embed
