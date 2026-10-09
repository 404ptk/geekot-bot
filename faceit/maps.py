"""Compact CS2 map statistics from the FACEIT Data API."""
import logging
import math
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands

logger = logging.getLogger(__name__)


def stat_number(stats, key):
    try:
        number = float(stats[key])
        return number if math.isfinite(number) and number >= 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def parse_map_stats(payload):
    maps = []
    for segment in payload.get('segments', []):
        if segment.get('type') != 'Map' or segment.get('mode') != '5v5':
            continue
        stats = segment.get('stats') or {}
        matches = stat_number(stats, 'Matches')
        if matches is None or matches < 1:
            continue
        winrate = stat_number(stats, 'Win Rate %')
        wins = stat_number(stats, 'Wins')
        if winrate is None and wins is not None:
            winrate = wins / matches * 100
        maps.append({
            'name': segment.get('label') or 'Nieznana mapa',
            'matches': int(matches),
            'winrate': winrate,
            'kd': stat_number(stats, 'Average K/D Ratio'),
            'adr': stat_number(stats, 'ADR'),
            'hs': stat_number(stats, 'Average Headshots %'),
        })
    maps.sort(key=lambda item: (
        item['winrate'] if item['winrate'] is not None else -1,
        item['adr'] if item['adr'] is not None else -1,
        item['matches'],
    ), reverse=True)
    return maps


def format_stat(value, precision=0, suffix=''):
    return '—' if value is None else f'{value:.{precision}f}{suffix}'


class MapsView(discord.ui.LayoutView):
    def __init__(self, player, maps):
        super().__init__(timeout=None)
        self.player = player
        self.maps = maps
        self.render()

    def render(self):
        self.clear_items()
        nickname = self.player['nickname']
        profile = f'https://www.faceit.com/en/players/{quote(nickname, safe="")}'
        title = discord.ui.TextDisplay(
            f'## 🗺️ Mapy · [{discord.utils.escape_markdown(nickname)}]({profile})\n'
            '-# CS2 · 5v5 · według winrate, przy remisie ADR'
        )
        container = discord.ui.Container(accent_color=0xFF5500)
        avatar = self.player.get('avatar')
        if isinstance(avatar, str) and avatar.startswith(('https://', 'http://')):
            container.add_item(discord.ui.Section(title, accessory=discord.ui.Thumbnail(avatar)))
        else:
            container.add_item(title)
        container.add_item(discord.ui.Separator())
        map_lines = []
        for index, item in enumerate(self.maps, start=1):
            name = discord.utils.escape_markdown(item['name'])
            map_lines.append(
                f"**{index}. {name}** · {item['matches']} meczów\n"
                f"WR **{format_stat(item['winrate'], suffix='%')}** · "
                f"K/D **{format_stat(item['kd'], 2)}** · "
                f"ADR **{format_stat(item['adr'], 1)}** · "
                f"HS **{format_stat(item['hs'], suffix='%')}**"
            )
        container.add_item(discord.ui.TextDisplay("\n\n".join(map_lines)))
        container.add_item(discord.ui.Separator())
        self.footer = discord.ui.TextDisplay(
            '-# Statystyki zbiorcze'
        )
        container.add_item(self.footer)
        self.add_item(container)


def register_maps_command(tree, guild, faceit_nick_autocomplete):
    @tree.command(name='maps', description='Pokazuje statystyki map CS2 gracza Faceit', guild=guild)
    @app_commands.describe(nick='Nick gracza Faceit')
    @app_commands.autocomplete(nick=faceit_nick_autocomplete)
    async def maps(interaction: discord.Interaction, nick: str):
        import faceit_utils as fu

        await interaction.response.defer()
        if not fu.FACEIT_API_KEY:
            await interaction.followup.send('Brak klucza API Faceit.', ephemeral=True)
            return
        try:
            async with aiohttp.ClientSession(
                headers={'Authorization': f'Bearer {fu.FACEIT_API_KEY}'},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as session:
                async with session.get('https://open.faceit.com/data/v4/players', params={'nickname': nick}) as response:
                    if response.status == 404:
                        await interaction.followup.send('Nie znaleziono gracza o podanym nicku.', ephemeral=True)
                        return
                    response.raise_for_status()
                    player = await response.json()
                async with session.get(
                    f"https://open.faceit.com/data/v4/players/{player['player_id']}/stats/cs2"
                ) as response:
                    if response.status == 404:
                        await interaction.followup.send('Brak statystyk map CS2 dla tego gracza.', ephemeral=True)
                        return
                    response.raise_for_status()
                    payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
            logger.warning('Cannot retrieve FACEIT map statistics')
            await interaction.followup.send('Nie udało się pobrać statystyk Faceit. Spróbuj ponownie za chwilę.', ephemeral=True)
            return
        map_stats = parse_map_stats(payload)
        if not map_stats:
            await interaction.followup.send('Brak rozegranych map CS2 w trybie 5v5.', ephemeral=True)
            return
        view = MapsView(player, map_stats)
        await interaction.followup.send(
            view=view, allowed_mentions=discord.AllowedMentions.none(),
        )
