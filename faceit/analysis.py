"""Exploratory CS2 history analysis, with bounded fetching and a private V2 panel."""
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import logging
import math
from statistics import mean, stdev
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands

from config.conf_faceit_live_settings import load_config
from faceit.maps import stat_number

logger = logging.getLogger(__name__)
WARSAW = ZoneInfo('Europe/Warsaw')
API = 'https://open.faceit.com/data/v4'
CACHE_SECONDS = 900
_CACHE = {}
_FETCHING = set()
_FETCH_SLOTS = asyncio.Semaphore(3)


class AnalysisError(Exception):
    """An error that can be explained to the user without API internals."""


async def api_get(session, path, params=None, *, missing_ok=False):
    for attempt in range(3):
        async with session.get(API + path, params=params) as response:
            if response.status == 404:
                if missing_ok:
                    return {'items': []}
                raise AnalysisError('Nie znaleziono gracza lub jego danych CS2 na Faceicie.')
            if response.status == 429:
                if attempt == 2:
                    raise AnalysisError('Faceit ograniczył liczbę zapytań. Spróbuj za chwilę.')
                await asyncio.sleep(attempt + 1)
                continue
            response.raise_for_status()
            payload = await response.json()
            if not isinstance(payload, dict):
                raise AnalysisError('Faceit zwrócił nieprawidłowe dane. Spróbuj później.')
            return payload


async def fetch_dataset(key, nickname):
    async with aiohttp.ClientSession(
        headers={'Authorization': f'Bearer {key}'},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as session:
        player = await api_get(session, '/players', {'nickname': nickname})
        player_id = player.get('player_id')
        if not player_id or not (player.get('games') or {}).get('cs2'):
            raise AnalysisError('Ten gracz nie ma profilu CS2 na Faceicie.')

        async def pages(path, count, params=None):
            result = []
            for offset in range(0, count, 100):
                payload = await api_get(session, path, dict(params or {}, limit=100, offset=offset), missing_ok=True)
                items = payload.get('items')
                if not isinstance(items, list):
                    raise AnalysisError('Faceit zwrócił nieprawidłową historię meczów.')
                result.extend(items)
                if len(items) < 100:
                    break
            return result

        # The player-statistics endpoint only accepts offsets through 200.
        history_task = asyncio.create_task(pages(f'/players/{player_id}/history', 500, {'game': 'cs2'}))
        stats_task = asyncio.create_task(pages(f'/players/{player_id}/games/cs2/stats', 300))
        tasks = (history_task, stats_task)
        try:
            history, stats = await asyncio.gather(*tasks)
        except BaseException:
            # Do not leave a second fetch running after failure or cancellation.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return {'player': player, 'history': history, 'stats': stats, 'fetched_at': time.time()}


@dataclass
class Match:
    id: str
    start: float
    end: float
    win: bool
    own_score: int | None
    opponent_score: int | None
    mates: dict
    map: str | None
    adr: float | None
    kd: float | None
    kr: float | None
    hs: float | None
    kills: float | None
    deaths: float | None
    rounds: float | None
    half: float | None
    session: int = 0
    previous: bool | None = None

    @property
    def date(self):
        return datetime.fromtimestamp(self.start, WARSAW)


def parse_matches(dataset):
    player_id = dataset['player']['player_id']
    stats = {}
    for entry in dataset.get('stats', []):
        values = entry.get('stats') or {}
        if values.get('Match Id'):
            stats[values['Match Id']] = values
    matches = {}
    skipped = 0
    for item in dataset.get('history', []):
        try:
            if item.get('status') != 'finished' or item.get('game_id') != 'cs2':
                continue
            if item.get('game_mode') not in (None, '', '5v5'):
                continue
            teams = item['teams']
            side = next((side for side, team in teams.items()
                         if any(p.get('player_id') == player_id for p in team.get('players', []))), None)
            if side is None or item['results'].get('winner') not in teams:
                skipped += 1
                continue
            start, end = float(item['started_at']), float(item['finished_at'])
            if not math.isfinite(start) or not math.isfinite(end) or start <= 0 or end < start:
                skipped += 1
                continue
            datetime.fromtimestamp(start, WARSAW)
            values = stats.get(item['match_id'], {})
            scores = item['results'].get('score') or {}
            own = stat_number(scores, side)
            opponent = next((stat_number(scores, other) for other in teams if other != side), None)
            hs = stat_number(values, 'Headshots %')
            half = stat_number(values, 'First Half Score')
            matches[item['match_id']] = Match(
                id=item['match_id'], start=start, end=end, win=item['results']['winner'] == side,
                own_score=int(own) if own is not None else None,
                opponent_score=int(opponent) if opponent is not None else None,
                mates={p['player_id']: p.get('nickname') or 'Nieznany gracz'
                       for p in teams[side].get('players', []) if p.get('player_id') and p['player_id'] != player_id},
                map=values.get('Map') or None,
                adr=stat_number(values, 'ADR'), kd=stat_number(values, 'K/D Ratio'),
                kr=stat_number(values, 'K/R Ratio'), hs=hs if hs is not None and hs <= 100 else None,
                kills=stat_number(values, 'Kills'), deaths=stat_number(values, 'Deaths'),
                rounds=stat_number(values, 'Rounds'), half=half if half is not None and half <= 12 else None,
            )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            skipped += 1
    return sorted(matches.values(), key=lambda match: match.start), skipped


def session_groups(matches, gap_minutes=90):
    groups = []
    previous = None
    for match in matches:
        gap = (match.start - previous.end) / 60 if previous else None
        if gap is None or gap < 0 or gap > gap_minutes:
            groups.append([])
        groups[-1].append(match)
        previous = match
    return groups


def average(matches, key):
    values = [getattr(match, key) for match in matches if getattr(match, key) is not None]
    return mean(values) if values else None


def winrate(matches):
    return sum(match.win for match in matches) / len(matches) * 100 if matches else None


def fmt(value, precision=1):
    return '—' if value is None else f'{value:.{precision}f}'


def escaped(value):
    # Keep user-controlled names within component limits and out of markdown syntax.
    return discord.utils.escape_markdown(str(value).replace('\n', ' ')[:100])


def result_line(matches):
    wins = sum(match.win for match in matches)
    return f'**WR:** `{fmt(winrate(matches))}%` ▫️ **W/L:** `{wins}/{len(matches) - wins}` ▫️ **Mecze:** `{len(matches)}`'


def metrics_line(matches):
    n = sum(match.adr is not None for match in matches)
    text = f"**ADR:** `{fmt(average(matches, 'adr'))}` ▫️ **K/D:** `{fmt(average(matches, 'kd'), 2)}` ▫️ **K/R:** `{fmt(average(matches, 'kr'), 2)}`"
    if n < len(matches):
        text += f'\n-# ADR dostępny w {n} z {len(matches)} meczów.'
    return text


def wilson(matches):
    n = len(matches)
    if not n:
        return None
    p = sum(match.win for match in matches) / n
    z = 1.96
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0, (center - radius) * 100), min(100, (center + radius) * 100)


def interval_line(matches):
    bounds = wilson(matches)
    return f'-# Przedział WR 95%: {fmt(bounds[0])}–{fmt(bounds[1])}%' if bounds else '-# Brak danych.'


def pack_pages(blocks, limit=3100):
    pages = []
    current = ''
    block_count = 0
    for block in blocks:
        # A malformed description must never break Discord's text limit.
        if len(block) > limit:
            block = block[:limit - 1] + '…'
        if current and (len(current) + len(block) + 2 > limit or block_count >= 7):
            pages.append(current)
            current = ''
            block_count = 0
        current = f'{current}\n\n{block}' if current else block
        block_count += 1
    if current:
        pages.append(current)
    return pages or ['Za mało danych do wyświetlenia tej części analizy.']


def paired_sessions(matches, gap=90):
    # Exclude the oldest session: the fetched history may cut off its beginning.
    return [group for group in session_groups(matches, gap)[1:]
            if len(group) >= 2 and all(match.adr is not None for match in group)]


def session_differences(groups):
    return [mean(match.adr for match in group[1:]) - group[0].adr for group in groups]


def mean_interval(values):
    if len(values) < 2:
        return None
    radius = 1.96 * stdev(values) / math.sqrt(len(values))
    return mean(values) - radius, mean(values) + radius


def stable_contrast(matches, classify, a, b, minimum=15):
    """Require a supported direction in both chronological halves."""
    halves = (matches[:len(matches) // 2], matches[len(matches) // 2:])
    differences = []
    for half in halves:
        left = [m for m in half if classify(m) == a]
        right = [m for m in half if classify(m) == b]
        if min(len(left), len(right)) < minimum:
            return False
        differences.append(winrate(right) - winrate(left))
    return all(difference >= 5 for difference in differences) or all(difference <= -5 for difference in differences)


class AnalysisReport:
    def __init__(self, dataset, known_players):
        self.player = dataset['player']
        self.fetched_at = dataset['fetched_at']
        self.matches, self.skipped = parse_matches(dataset)
        if not self.matches:
            raise AnalysisError('Brak zakończonych meczów CS2 5v5 do analizy.')
        self.known = {name.casefold() for name in known_players}
        self.groups = session_groups(self.matches)
        for group in self.groups:
            for index, match in enumerate(group):
                match.session = index + 1
                match.previous = group[index - 1].win if index else None
        self.details = [match for match in self.matches if match.map is not None]
        self.sections = self.build_sections()

    def known_count(self, match):
        return sum(name.casefold() in self.known for name in match.mates.values())

    def build_sections(self):
        matches = self.matches
        first, last = matches[0].date, matches[-1].date
        overview = [
            f'**Wyniki całej próby**\n{result_line(matches)}\n{interval_line(matches)}',
            f'**Statystyki osobiste**\n{metrics_line(matches)}\n**HS:** `{fmt(average(matches, "hs"))}%`\n-# Średnie z dostępnych statystyk meczowych.',
        ]
        for count in (20, 50, 100):
            if len(matches) >= count:
                recent = matches[-count:]
                overview.append(f'**Ostatnie {count} meczów**\n{result_line(recent)}\n{metrics_line(recent)}')
        longest = {True: 0, False: 0}
        streak, previous = 0, None
        for match in matches:
            streak = streak + 1 if previous == match.win else 1
            longest[match.win] = max(longest[match.win], streak)
            previous = match.win
        overview.append(f'**Serie w pobranej historii**\nNajdłuższa: {longest[True]} wygranych / {longest[False]} porażek z rzędu.')

        maps = defaultdict(list)
        for match in self.details:
            maps[match.map].append(match)
        map_blocks = ['**Mapy z ostatnich dostępnych statystyk meczowych**\nKolejność według liczby rozegranych spotkań. Wyniki dotyczą tej próby, a nie całej kariery.']
        for name, group in sorted(maps.items(), key=lambda pair: -len(pair[1])):
            map_blocks.append(f'**{escaped(name.removeprefix("de_"))}**\n{result_line(group)}\n{metrics_line(group)}\n{interval_line(group)}' + ('\n-# Mała próba — poniżej 20 meczów.' if len(group) < 20 else ''))
        if not maps:
            map_blocks.append('Brak statystyk map w pobranych danych.')

        time_blocks = ['**Pora rozpoczęcia · czas polski**\nKażdy mecz przypisano do godziny rozpoczęcia. Różnice mogą wynikać ze składów i przeciwników.']
        for label, test in (
            ('06:00–17:59', lambda hour: 6 <= hour < 18),
            ('18:00–21:59', lambda hour: 18 <= hour < 22),
            ('22:00–05:59', lambda hour: hour >= 22 or hour < 6),
        ):
            group = [m for m in matches if test(m.date.hour)]
            if group:
                time_blocks.append(f'**{label}**\n{result_line(group)}\n{metrics_line(group)}')
        weekdays = ('Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela')
        for day, label in enumerate(weekdays):
            group = [m for m in matches if m.date.weekday() == day]
            if group:
                time_blocks.append(f'**{label}**\n{result_line(group)}')

        session_blocks = ['**Przebieg sesji**\nNowa sesja zaczyna się po przerwie ponad 90 minut. Nakładające się czasy rozdzielają sesje. Najstarszą sesję pomijamy w porównaniach, bo historia może urywać jej początek.']
        # Descriptive positions use the same complete session boundaries.
        supported = [m for group in self.groups[1:] for m in group]
        for position in range(1, 6):
            group = [m for m in supported if (m.session == position if position < 5 else m.session >= 5)]
            if group:
                label = f'Mecz {position}' if position < 5 else 'Mecz 5 i kolejne'
                session_blocks.append(f'**{label}**\n{result_line(group)}\n{metrics_line(group)}')
        pairs = paired_sessions(matches)
        differences = session_differences(pairs)
        if pairs:
            bounds = mean_interval(differences)
            session_blocks.append(
                f'**Porównanie w tych samych sesjach · {len(pairs)} sesji**\n'
                f'Pierwszy mecz: **{fmt(mean(group[0].adr for group in pairs))} ADR**\n'
                f'Kolejne: **{fmt(mean(mean(m.adr for m in group[1:]) for group in pairs))} ADR**\n'
                f'Różnica: **{mean(differences):+.1f} ADR**' +
                (f'\n-# Przedział różnicy 95%: {bounds[0]:+.1f} do {bounds[1]:+.1f} ADR.' if bounds else '') +
                '\n-# Każda sesja ma taką samą wagę. Mapa, przeciwnicy i skład mogą się różnić.')

        roster_blocks = ['**Współgracze i skład**\nObecność w drużynie nie dowodzi wspólnego party. „Znajomi” to gracze z listy Faceit w konfiguracji bota; brak ich w meczu nie oznacza solo queue.']
        for count in range(5):
            group = [m for m in matches if self.known_count(m) == count]
            if group:
                roster_blocks.append(f'**{count} znajomych w drużynie**\n{result_line(group)}\n{metrics_line(group)}')
        mates = defaultdict(list)
        names = {}
        for match in matches:
            for player_id, nickname in match.mates.items():
                mates[player_id].append(match)
                names[player_id] = nickname
        for player_id, group in sorted(mates.items(), key=lambda pair: -len(pair[1]))[:15]:
            if len(group) < 5:
                continue
            without = [m for m in matches if player_id not in m.mates]
            roster_blocks.append(f'**Z {escaped(names[player_id])}**\n{result_line(group)}\n{metrics_line(group)}\n-# Bez tej osoby: {fmt(winrate(without))}% WR ({len(without)} meczów).')

        form_blocks = ['**Forma w czasie**\nPorównujemy wyniki i statystyki, bez rekonstrukcji historycznego ELO. Średnie dotyczą dostępnych pól; poziom przeciwników może się zmieniać.']
        for label, group in [('Starsza połowa', matches[:len(matches) // 2]), ('Nowsza połowa', matches[len(matches) // 2:])]:
            if group:
                form_blocks.append(f'**{label}**\n{result_line(group)}\n{metrics_line(group)}')
        months = defaultdict(list)
        for match in matches:
            months[match.date.strftime('%Y-%m')].append(match)
        for label, group in sorted(months.items(), reverse=True):
            form_blocks.append(f'**{label}**\n{result_line(group)}\n{metrics_line(group)}')
        for previous, label in [(False, 'Następny mecz po porażce'), (True, 'Następny mecz po zwycięstwie')]:
            group = [m for m in supported if m.previous is previous]
            if group:
                form_blocks.append(f'**{label} · w tej samej sesji**\n{result_line(group)}\n{metrics_line(group)}')
        for label, test in [('Dogrywki', lambda a, b: max(a, b) > 13), ('Bliskie wyniki bez dogrywki', lambda a, b: max(a, b) == 13 and min(a, b) >= 10)]:
            group = [m for m in matches if m.own_score is not None and m.opponent_score is not None and test(m.own_score, m.opponent_score)]
            if group:
                form_blocks.append(f'**{label}**\n{result_line(group)}')
        for label, test in [('Prowadzenie co najmniej 8:4 po połowie', lambda score: score >= 8), ('Remis 6:6 po połowie', lambda score: score == 6), ('Strata co najmniej 4:8 po połowie', lambda score: score <= 4)]:
            group = [m for m in matches if m.half is not None and test(m.half)]
            if group:
                form_blocks.append(f'**{label}**\n{result_line(group)}')

        sources = [
            '**Zakres i źródło**\nOficjalne FACEIT Data API. Pobieramy do 500 ostatnich wpisów historii CS2 i do 300 statystyk meczowych. To nie jest pełna historia kariery.',
            f'**Kompletność danych**\nZakończone mecze 5v5: {len(matches)}. Mapa dostępna: {len(self.details)}. '
            f'ADR: {sum(m.adr is not None for m in matches)}. K/D: {sum(m.kd is not None for m in matches)}. '
            f'Pominięte nieprawidłowe wpisy: {self.skipped}.\nBrak pola oznacza brak danych, a nie zero.',
            '**Jak czytać liczby**\nWR to odsetek wygranych. ADR, K/D, K/R i HS to średnie z dostępnych meczów, bez ważenia długością meczu. Przedziały WR pokazują niepewność przy małej próbie.',
            '**Jak powstają obserwacje**\nWymagamy minimalnej próby, wyraźnej różnicy oraz zgodnego kierunku w starszej i nowszej części historii. Dla sesji sprawdzamy też różne długości przerw. Nie wymuszamy liczby wniosków ani nie tworzymy osobnej obserwacji dla każdej mapy.',
            '**Ograniczenia**\nTo analiza eksploracyjna: wybór spośród wielu zależności zwiększa ryzyko przypadkowego wyniku. Przedziały nie są skorygowane o ten wybór. Potwierdzenie wymaga nowych meczów. Nie wnioskujemy o winie gracza, taktyce, party ani historycznym ELO. Nie analizujemy dem.',
            f'**Aktualność**\nDane pobrano <t:{int(self.fetched_at)}:R>. Pamięć podręczna działa przez 15 minut. Godziny: Europe/Warsaw.',
        ]
        sections = (
            ('overview', overview), ('maps', map_blocks), ('time', time_blocks),
            ('sessions', session_blocks), ('roster', roster_blocks), ('form', form_blocks),
            ('insights', self.insights()), ('sources', sources),
        )
        result = {}
        for key, blocks in sections:
            styled = []
            for index, block in enumerate(blocks):
                heading, separator, body = block.partition('\n')
                if heading.startswith('**') and heading.endswith('**'):
                    heading = f'### {heading[2:-2]}'
                if index == 0 and key not in ('overview', 'sources'):
                    body = '\n'.join(f'-# {line}' for line in body.splitlines())
                styled.append(heading + separator + body)
            result[key] = pack_pages(styled)
        return result

    def insights(self):
        blocks = ['**Obserwacje z tej historii**\nTo wskazówki do sprawdzenia w kolejnych meczach, nie dowody przyczyn. Każda karta dotyczy innego rodzaju zależności.']
        pairs = paired_sessions(self.matches)
        diffs = session_differences(pairs)
        if len(pairs) >= 30:
            bounds = mean_interval(diffs)
            older, newer = diffs[:len(diffs) // 2], diffs[len(diffs) // 2:]
            sign = 1 if mean(diffs) > 0 else -1
            stable = all(sign * mean(part) > 0 for part in (older, newer))
            sensitivity = [session_differences(paired_sessions(self.matches, gap)) for gap in (45, 60, 120, 180)]
            robust = all(len(values) >= 20 and sign * mean(values) >= 3 for values in sensitivity)
            if abs(mean(diffs)) >= 5 and bounds[0] * bounds[1] > 0 and stable and robust:
                title = 'Pierwszy mecz ma słabsze statystyki' if sign > 0 else 'Statystyki spadają w dalszej części sesji'
                blocks.append(f'**{title}**\n**Zmiana ADR:** `{mean(diffs):+.1f}` ▫️ **Sesje:** `{len(pairs)}`\n'
                              f'**Starsza próba:** `{mean(older):+.1f}` ▫️ **Nowsza:** `{mean(newer):+.1f}`\n'
                              '-# Porównanie kolejnych meczów z pierwszym w tych samych sesjach.\n'
                              '-# Kierunek utrzymuje się przy różnych przerwach; mapa, skład i przeciwnicy mogą to tłumaczyć.')

        def bucket(match):
            count = self.known_count(match)
            return 'partial' if count in (1, 2) else str(count)
        partial = [m for m in self.matches if bucket(m) == 'partial']
        candidates = []
        for other in ('0', '4'):
            group = [m for m in self.matches if bucket(m) == other]
            if min(len(partial), len(group)) >= 40 and stable_contrast(self.matches, bucket, 'partial', other):
                difference = winrate(group) - winrate(partial)
                if abs(difference) >= 10:
                    candidates.append((abs(difference), other, group))
        if candidates:
            _, other, group = max(candidates, key=lambda candidate: candidate[0])
            label = 'bez znajomych z listy' if other == '0' else 'z czterema znajomymi z listy'
            blocks.append(f'**Konfiguracja składu wiąże się z różnicą wyników**\n'
                          f'**1–2 znajomych:** `{fmt(winrate(partial))}% WR` · `{len(partial)} meczów`\n'
                          f'**{label.capitalize()}:** `{fmt(winrate(group))}% WR` · `{len(group)} meczów`\n'
                          '-# Kierunek różnicy utrzymuje się w obu połowach historii.\n'
                          '-# Nie znamy party ani historycznego poziomu przeciwników; to nie wskazanie winnego.')

        def late(match):
            return match.date.hour >= 22 or match.date.hour < 6
        day = [m for m in self.matches if not late(m)]
        night = [m for m in self.matches if late(m)]
        if min(len(day), len(night)) >= 40 and abs(winrate(night) - winrate(day)) >= 10 and stable_contrast(self.matches, late, False, True):
            blocks.append(f'**Wyniki różnią się między porami dnia**\n'
                          f'**22:00–05:59:** `{fmt(winrate(night))}% WR` · `{len(night)} meczów`\n'
                          f'**Pozostałe godziny:** `{fmt(winrate(day))}% WR` · `{len(day)} meczów`\n'
                          '-# Kierunek utrzymuje się w obu połowach historii. Składy i przeciwnicy mogą się różnić.')

        # Inspect only the most frequently played map, rather than searching
        # every map for whichever happens to produce the most striking number.
        if len(self.details) >= 100:
            name, count = Counter(m.map for m in self.details).most_common(1)[0]
            group = [m for m in self.details if m.map == name]
            others = [m for m in self.details if m.map != name]
            adr, baseline = average(group, 'adr'), average(others, 'adr')
            halves = (self.details[:len(self.details) // 2], self.details[len(self.details) // 2:])
            repeated = all(len(part := [m for m in half if m.map == name]) >= 20 and winrate(part) < 50 for half in halves)
            if count >= 50 and adr is not None and baseline is not None and adr >= baseline and winrate(group) <= 47 and repeated:
                blocks.append(f'**Najczęstsza mapa: dobre liczby, mniej wygranych**\n'
                              f'**{escaped(name.removeprefix("de_"))}** · **ADR:** `{fmt(adr)}` ▫️ **WR:** `{fmt(winrate(group))}%` ▫️ **Mecze:** `{count}`\n'
                              '-# ADR co najmniej taki jak na pozostałych mapach, WR poniżej 50% w obu częściach próby.\n'
                              '-# Nie dowodzi to braku wpływu na rundy — ADR nie opisuje całej gry.')
        if len(blocks) == 1:
            blocks.append('**Brak wyraźnych zależności**\nTa próba nie spełnia warunków dla mocniejszych obserwacji. Zwykłe statystyki znajdziesz w pozostałych kategoriach.')
        return blocks


SECTIONS = (
    ('overview', '📊 Przegląd'), ('insights', '💡 Obserwacje'), ('maps', '🗺️ Mapy'),
    ('sessions', '🎮 Sesje'), ('roster', '👥 Składy'), ('time', '🕒 Pory grania'),
    ('form', '📈 Forma i wyniki'), ('sources', 'ℹ️ Dane i ograniczenia'),
)


class AnalysisSelect(discord.ui.Select):
    def __init__(self, selected):
        super().__init__(placeholder='Wybierz część analizy…', options=[
            discord.SelectOption(label=label, value=key, default=key == selected) for key, label in SECTIONS])

    async def callback(self, interaction):
        view = self.view
        view.section, view.page = self.values[0], 0
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class AnalysisPageButton(discord.ui.Button):
    def __init__(self, direction, disabled):
        super().__init__(label='Poprzednia' if direction < 0 else 'Następna',
                         style=discord.ButtonStyle.secondary, disabled=disabled)
        self.direction = direction

    async def callback(self, interaction):
        view = self.view
        view.page += self.direction
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class AnalysisView(discord.ui.LayoutView):
    def __init__(self, owner_id, report):
        super().__init__(timeout=600)
        self.owner_id, self.report = owner_id, report
        self.section, self.page = 'overview', 0
        self.message = None
        self.render()

    def render(self):
        self.clear_items()
        pages = self.report.sections[self.section]
        self.page = max(0, min(self.page, len(pages) - 1))
        player = self.report.player['nickname']
        profile = f'https://www.faceit.com/en/players/{quote(player, safe="")}'
        title = dict(SECTIONS)[self.section]
        container = discord.ui.Container(accent_color=0xFF5500)
        first, last = self.report.matches[0].date, self.report.matches[-1].date
        header = discord.ui.TextDisplay(
            f'# Analiza [{escaped(player)}]({profile})\n'
            f'**{title}**\n'
            f'-# {first:%d.%m.%Y}–{last:%d.%m.%Y} · {len(self.report.matches)} meczów CS2 5v5'
        )
        avatar = self.report.player.get('avatar')
        if isinstance(avatar, str) and avatar.startswith(('https://', 'http://')):
            container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(avatar)))
        else:
            container.add_item(header)
        container.add_item(discord.ui.Separator())
        for index, block in enumerate(pages[self.page].split('\n\n')):
            if index:
                container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small))
            container.add_item(discord.ui.TextDisplay(block))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(AnalysisSelect(self.section)))
        if len(pages) > 1:
            container.add_item(discord.ui.TextDisplay(f'-# Strona {self.page + 1} z {len(pages)}'))
            container.add_item(discord.ui.ActionRow(
                AnalysisPageButton(-1, self.page == 0), AnalysisPageButton(1, self.page == len(pages) - 1)))
        self.footer = discord.ui.TextDisplay('-# Analiza dostępnej historii • panel prywatny • wygasa po 10 minutach bezczynności')
        container.add_item(self.footer)
        self.add_item(container)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message('Otwórz własny panel przez /analiza.', ephemeral=True)
        return False

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Select, discord.ui.Button)):
                item.disabled = True
        self.footer.content = '-# Panel wygasł. Otwórz nowy przez /analiza.'
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        logger.error('Analysis panel failed', exc_info=(type(error), error, error.__traceback__))
        try:
            message = 'Nie udało się przełączyć panelu. Otwórz nowy przez /analiza.'
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning('Could not deliver analysis panel error response', exc_info=True)


async def get_dataset(key, nickname):
    cache_key = nickname.casefold()
    now = time.monotonic()
    for name, (created, _) in list(_CACHE.items()):
        if now - created >= CACHE_SECONDS:
            del _CACHE[name]
    if cache_key in _CACHE:
        return _CACHE[cache_key][1]
    if cache_key in _FETCHING:
        raise AnalysisError('Analiza tego gracza już się pobiera. Spróbuj ponownie za chwilę.')
    if _FETCH_SLOTS.locked():
        raise AnalysisError('Bot pobiera już kilka analiz. Spróbuj za chwilę.')
    _FETCHING.add(cache_key)
    try:
        async with _FETCH_SLOTS:
            dataset = await asyncio.wait_for(fetch_dataset(key, nickname), timeout=90)
        if len(_CACHE) >= 20:
            del _CACHE[min(_CACHE, key=lambda name: _CACHE[name][0])]
        _CACHE[cache_key] = (time.monotonic(), dataset)
        return dataset
    finally:
        _FETCHING.discard(cache_key)


def register_analysis_command(tree, guild, faceit_nick_autocomplete):
    @tree.command(name='analiza', description='Analizuje historię CS2: mapy, sesje, składy, formę i ciekawe zależności', guild=guild)
    @app_commands.describe(nick='Nick gracza Faceit')
    @app_commands.autocomplete(nick=faceit_nick_autocomplete)
    async def analiza(interaction: discord.Interaction, nick: str):
        import faceit_utils as fu

        await interaction.response.defer(ephemeral=True)
        nickname = nick.strip()
        if not nickname or len(nickname) > 100:
            await interaction.followup.send('Podaj prawidłowy nick gracza Faceit.', ephemeral=True)
            return
        if not fu.FACEIT_API_KEY:
            await interaction.followup.send('Brak klucza API Faceit.', ephemeral=True)
            return
        try:
            dataset = await get_dataset(fu.FACEIT_API_KEY, nickname)
            report = AnalysisReport(dataset, load_config()['players'])
        except AnalysisError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, OSError):
            logger.exception('Cannot build FACEIT analysis')
            await interaction.followup.send('Nie udało się przygotować analizy. Spróbuj ponownie za chwilę.', ephemeral=True)
            return
        view = AnalysisView(interaction.user.id, report)
        view.message = await interaction.followup.send(view=view, ephemeral=True, wait=True, allowed_mentions=discord.AllowedMentions.none())
