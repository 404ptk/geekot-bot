from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from jobs.config import load_config
from jobs.providers.isitfair import fetch_offers_page as isitfair_fetch_page
from jobs.providers.olx import fetch_offers_page as olx_fetch_page
from jobs.providers.custom import fetch_page

WARSAW_TZ = ZoneInfo('Europe/Warsaw')
API_STATUS_EMBED_TITLE = 'STAN API'


def check_all_services():
    results, failures = {}, {}
    for provider in load_config()['providers']:
        if not provider.get('enabled', True):
            continue
        name = provider['name']
        # Keep separate results even when two APIs have the same display name.
        label = f"{name} ({provider['id'][:8]})"
        try:
            if provider['type'] == 'isitfair':
                isitfair_fetch_page({'offer_status': 'active'}, 1, raise_errors=True, provider=provider)
            elif provider['type'] == 'olx':
                olx_fetch_page(None, 0, raise_errors=True, provider=provider)
            else:
                fetch_page(provider, 1)
            results[label] = True
        except Exception as exc:
            results[label] = False
            failures[label] = type(exc).__name__
    return results, failures


def build_status_embed(results, checked_at: datetime):
    lines = [f"{'🟢' if ok else '🔴'} {discord.utils.escape_markdown(discord.utils.escape_mentions(label))}" for label, ok in results.items()]
    embed = discord.Embed(title=API_STATUS_EMBED_TITLE,
                          description='\n'.join(lines) or 'Brak włączonych API.',
                          color=discord.Color.green() if results and all(results.values()) else discord.Color.orange())
    local = checked_at.astimezone(WARSAW_TZ)
    embed.set_footer(text=f"Ostatnia próba: {local.strftime('%H:%M %Z')}")
    return embed
