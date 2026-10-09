"""Optional country and regional positions from the FACEIT Data API."""
import asyncio
import logging

import requests

from faceit.common import get_country_flag_badge

logger = logging.getLogger(__name__)


def get_player_ranking(player_id, region, api_key, country=None):
    if not player_id or not region or not api_key:
        return None
    params = {'limit': 1}
    if country:
        params['country'] = country.lower()
    try:
        response = requests.get(
            f'https://open.faceit.com/data/v4/rankings/games/cs2/regions/{region}/players/{player_id}',
            headers={'Authorization': f'Bearer {api_key}'}, params=params, timeout=10,
        )
        if response.status_code != 200:
            logger.warning('FACEIT ranking unavailable: status=%s country=%s', response.status_code, country)
            return None
        position = response.json().get('position')
        return position if type(position) is int and position > 0 else None
    except (requests.RequestException, ValueError, AttributeError):
        logger.warning('Cannot read FACEIT ranking for country=%s', country)
        return None


async def player_ranking_line(player_data, api_key, guild=None):
    player_id = player_data.get('player_id')
    region = player_data.get('games', {}).get('cs2', {}).get('region')
    if not player_id or not region or not api_key:
        return ''
    country = str(player_data.get('country') or '').upper()
    valid_country = len(country) == 2 and country.isascii() and country.isalpha()
    regional_task = asyncio.to_thread(get_player_ranking, player_id, region, api_key)
    if valid_country:
        regional, national = await asyncio.gather(
            regional_task, asyncio.to_thread(get_player_ranking, player_id, region, api_key, country),
        )
    else:
        regional = await regional_task
        national = None
    parts = []
    if national is not None:
        parts.append(f'-# {get_country_flag_badge(guild, country)} #{national}')
    if regional is not None:
        parts.append(f'🌍 #{regional}')
    return ' | '.join(parts)
