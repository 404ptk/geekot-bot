"""Reusable history patterns. No nicknames or player-specific thresholds."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
import math
from statistics import mean, stdev

from faceit.analysis import average, escaped, fmt, winrate, stable_contrast, parse_matches


@dataclass(frozen=True)
class Pattern:
    key: str
    title: str
    lines: tuple
    descriptive: bool = False
    peer_id: str | None = None

    def text(self):
        label = ('Opis próby.' if self.descriptive else
                 'Sygnał do sprawdzenia; składy, mapy i przeciwnicy mogą się różnić.')
        return f'**{self.title}**\n' + '\n'.join(self.lines) + f'\n-# {label}'


# Each entry is a distinct question, not a variant for another name or map.
PATTERN_CATALOG = {
    'teammate_adr': 'ADR z konkretnym współgraczem',
    'teammate_hs': 'HS z konkretnym współgraczem',
    'teammate_map': 'Relacja ze współgraczem zależna od mapy',
    'teammate_session': 'Współgracz a przebieg sesji',
    'duo_combination': 'Dwie osoby razem kontra osobno',
    'lineup_change': 'Zmiana składu między kolejnymi meczami',
    'map_session': 'Przebieg sesji zależny od mapy',
    'break': 'Przerwa między meczami a zmiana ADR',
    'return': 'Powrót po tygodniu niegrania',
    'loss_streak': 'Następny mecz po serii porażek',
    'win_streak': 'Następny mecz po serii zwycięstw',
    'cold_start': 'Kontynuowanie sesji po słabym pierwszym meczu',
    'variability': 'Zmiana regularności ADR w czasie',
    'low_games': 'Częstotliwość meczów poniżej własnego poziomu',
    'multikills': 'Rozkład rund z multikillami',
    'hs_profile': 'Zmiana HS przy podobnym K/R',
    'survival': 'Zmiana liczby śmierci na rundę przy podobnym K/R',
    'overtime_adr': 'Osobiste statystyki w dogrywkach',
    'lead_conversion': 'Utrzymywanie prowadzenia po połowie',
    'map_pool': 'Koncentracja puli map i jej zmiana',
    'reciprocal': 'Obie osoby w dokładnie tych samych wspólnych meczach',
    'rival': 'Powtarzający się przeciwnik z nicku',
}


def numeric_contrast(rows, classify, key='adr', minimum=25, effect=5):
    """Bounded exploratory comparison with support in chronological halves."""
    usable = [m for m in rows if getattr(m, key) is not None]
    left = [m for m in usable if not classify(m)]
    right = [m for m in usable if classify(m)]
    if min(len(left), len(right)) < minimum:
        return None
    difference = average(right, key) - average(left, key)
    radius = 1.96 * math.sqrt(stdev([getattr(m, key) for m in left]) ** 2 / len(left) +
                             stdev([getattr(m, key) for m in right]) ** 2 / len(right))
    if abs(difference) < effect or abs(difference) <= radius:
        return None
    for half in (usable[:len(usable) // 2], usable[len(usable) // 2:]):
        a = [m for m in half if not classify(m)]
        b = [m for m in half if classify(m)]
        if min(len(a), len(b)) < 6:
            return None
        delta = average(b, key) - average(a, key)
        if delta * difference <= 0:
            return None
    return left, right, difference


def rate_contrast(rows, classify, minimum=30, effect=12):
    left = [m for m in rows if not classify(m)]
    right = [m for m in rows if classify(m)]
    if min(len(left), len(right)) < minimum:
        return None
    delta = winrate(right) - winrate(left)
    if abs(delta) < effect or not stable_contrast(rows, classify, False, True, minimum=8):
        return None
    return left, right, delta


def compare_line(label, rows, key='adr'):
    value = f'{fmt(average(rows, key))}%' if key == 'hs' else f'{fmt(average(rows, key))} {key.upper()}'
    return f'**{label}:** `{value}` · `{len(rows)} meczów`'


def best(cards):
    return [max(cards, key=lambda pair: pair[0])[1]] if cards else []


def collect_patterns(report):
    rows = report.matches
    stats = [m for m in rows if m.adr is not None]
    cards = []
    mates = Counter(uid for m in stats for uid in m.mates).most_common(6)
    maps = Counter(m.map for m in stats if m.map).most_common(3)
    previous = {current.id: prior for group in report.groups[1:] for prior, current in zip(group, group[1:])}
    continued = [m for m in rows if m.id in previous]

    # Inspect a fixed, small set of frequent teammates, and display at most
    # one result per question. Opposite-direction cards use the same rule.
    for key, metric, effect in [('teammate_adr', 'adr', 6), ('teammate_hs', 'hs', 7)]:
        candidates = []
        for uid, _ in mates:
            result = numeric_contrast(stats, lambda m: uid in m.mates, metric, effect=effect)
            if not result:
                continue
            apart, together, delta = result
            nick = escaped(together[-1].mates[uid])
            candidates.append((abs(delta), Pattern(key, f'{metric.upper()} w meczach z {nick}', (
                compare_line(f'Z {nick}', together, metric), compare_line('Bez tej osoby', apart, metric),
                f'-# Różnica {delta:+.1f}; kierunek powtarza się w obu częściach historii. Obecność nie potwierdza wspólnego party.',
            ), peer_id=uid)))
        cards.extend(best(candidates))

    # The command optionally fetches one teammate, never the entire server.
    # Join by match ID and check team membership in both histories.
    own_id = report.player['player_id']
    for dataset in report.peer_datasets:
        peer_id = dataset['player']['player_id']
        peer_rows, _ = parse_matches(dataset)
        peer_by_id = {m.id: m for m in peer_rows if own_id in m.mates and m.adr is not None}
        shared = [m for m in stats if peer_id in m.mates and m.id in peer_by_id]
        own_other = [m for m in stats if peer_id not in m.mates]
        peer_other = [m for m in peer_rows if own_id not in m.mates and m.adr is not None]
        if len(shared) < 20 or min(len(own_other), len(peer_other)) < 25:
            continue
        own_delta = average(shared, 'adr') - average(own_other, 'adr')
        peer_shared = [peer_by_id[m.id] for m in shared]
        peer_delta = average(peer_shared, 'adr') - average(peer_other, 'adr')
        if own_delta * peer_delta < 0 and min(abs(own_delta), abs(peer_delta)) >= 5:
            nick = escaped(dataset['player']['nickname'])
            own = escaped(report.player['nickname'])
            cards.append(Pattern('reciprocal', f'Wspólne mecze z {nick}: różne profile obu osób', (
                f'**{own}:** `{fmt(average(shared, "adr"))} ADR` · poza tym składem `{fmt(average(own_other, "adr"))}`',
                f'**{nick}:** `{fmt(average(peer_shared, "adr"))} ADR` · poza tym składem `{fmt(average(peer_other, "adr"))}`',
                f'-# Dokładnie {len(shared)} wspólnych ID meczów. Porównania poza wspólnym składem: {len(own_other)}/{len(peer_other)} meczów.',
                '-# Historia i poziom przeciwników poza wspólnymi meczami mogą być różne. To nie dowód zmiany ról ani odbierania sobie fragów.',
            ), True))
            break

    candidates = []
    for uid, _ in mates[:3]:
        for name, _ in maps[:2]:
            on_map = [m for m in stats if m.map == name]
            result = numeric_contrast(on_map, lambda m: uid in m.mates, minimum=15, effect=8)
            elsewhere = numeric_contrast([m for m in stats if m.map != name], lambda m: uid in m.mates, effect=5)
            if result and elsewhere and result[2] * elsewhere[2] < 0:
                apart, together, delta = result
                nick = escaped(together[-1].mates[uid])
                candidates.append((abs(delta - elsewhere[2]), Pattern('teammate_map', f'Z {nick} zależność zmienia się między mapami', (
                    f'**{escaped(name.removeprefix("de_"))}:** `{delta:+.1f} ADR` z tą osobą względem meczów bez niej',
                    f'**Pozostałe mapy:** `{elsewhere[2]:+.1f} ADR`',
                    f'-# Próby: {len(together)}/{len(apart)} na mapie, {len(elsewhere[1])}/{len(elsewhere[0])} poza nią. Kierunki utrzymują się w częściach prób.',
                ))))
    cards.extend(best(candidates))

    candidates = []
    for uid, _ in mates[:3]:
        with_uid = [m for m in stats if uid in m.mates and m.session <= 3]
        without_uid = [m for m in stats if uid not in m.mates and m.session <= 3]
        a = numeric_contrast(with_uid, lambda m: m.session > 1, minimum=15, effect=7)
        b = numeric_contrast(without_uid, lambda m: m.session > 1, minimum=15, effect=5)
        if a and b and a[2] * b[2] < 0:
            nick = escaped(a[1][-1].mates[uid])
            candidates.append((abs(a[2] - b[2]), Pattern('teammate_session', f'Przebieg sesji różni się z {nick}', (
                f'**Zmiana ADR z tą osobą:** `{a[2]:+.1f}` · **Bez niej:** `{b[2]:+.1f}`',
                f'-# Mecz 2–3 kontra pierwszy: próby z tą osobą {len(a[1])}/{len(a[0])}, bez niej {len(b[1])}/{len(b[0])}.',
                '-# To porównanie grup meczów, nie tych samych sesji; nie dowodzi wpływu współgracza.',
            ))))
    cards.extend(best(candidates))

    candidates = []
    for a, b in combinations([uid for uid, _ in mates[:3]], 2):
        at_least_one = [m for m in rows if a in m.mates or b in m.mates]
        result = rate_contrast(at_least_one, lambda m: a in m.mates and b in m.mates)
        if result:
            single, both, delta = result
            last = both[-1]
            title = f'{escaped(last.mates[a])} i {escaped(last.mates[b])} razem'
            candidates.append((abs(delta), Pattern('duo_combination', title, (
                f'**Obie osoby:** `{fmt(winrate(both))}% WR` · `{len(both)} meczów`',
                f'**Tylko jedna z nich:** `{fmt(winrate(single))}% WR` · `{len(single)} meczów`',
                '-# Nie porównujemy do meczów bez obu osób. Pozostały skład i poziom przeciwników nie są kontrolowane.',
            ))))
    cards.extend(best(candidates))

    result = numeric_contrast(continued, lambda m: set(m.mates) != set(previous[m.id].mates), effect=6)
    if result:
        same, changed, _ = result
        cards.append(Pattern('lineup_change', 'Statystyki różnią się po zmianie składu w sesji', (
            compare_line('Skład bez zmian', same), compare_line('Co najmniej jedna zmiana', changed),
            '-# Porównujemy kolejne mecze w sesji; nie znamy przyczyny zmiany składu.',
        )))

    candidates = []
    for name, _ in maps:
        a = numeric_contrast([m for m in stats if m.map == name], lambda m: m.session >= 3, minimum=15, effect=8)
        b = numeric_contrast([m for m in stats if m.map != name], lambda m: m.session >= 3, effect=5)
        if a and b and a[2] * b[2] < 0:
            candidates.append((abs(a[2] - b[2]), Pattern('map_session', 'Przebieg sesji różni się między mapami', (
                f'**{escaped(name.removeprefix("de_"))}:** `{a[2]:+.1f} ADR` · **Pozostałe mapy:** `{b[2]:+.1f}`',
                f'-# Mecz 3+ kontra 1–2; próby {len(a[1])}/{len(a[0])} i {len(b[1])}/{len(b[0])}.',
                '-# To grupy spotkań, nie dowód zmęczenia ani porównanie tych samych sesji.',
            ))))
    cards.extend(best(candidates))

    # Compare changes, rather than absolute ADR, for a break between games.
    break_changes = []
    for m in continued:
        p = previous[m.id]
        if m.adr is not None and p.adr is not None:
            break_changes.append((m, m.adr - p.adr, (m.start - p.end) / 60 >= 15))
    quick = [value for _, value, pause in break_changes if not pause]
    rested = [value for _, value, pause in break_changes if pause]
    if min(len(quick), len(rested)) >= 25:
        delta = mean(rested) - mean(quick)
        radius = 1.96 * math.sqrt(stdev(quick) ** 2 / len(quick) + stdev(rested) ** 2 / len(rested))
        parts = [break_changes[:len(break_changes) // 2], break_changes[len(break_changes) // 2:]]
        supported = all(len(x := [v for _, v, pause in part if pause]) >= 8 and
                        len(y := [v for _, v, pause in part if not pause]) >= 8 and (mean(x) - mean(y)) * delta > 0 for part in parts)
        if abs(delta) >= 7 and abs(delta) > radius and supported:
            cards.append(Pattern('break', 'Zmiana ADR różni się po krótkiej przerwie', (
                f'**Przerwa 15–90 min:** `{mean(rested):+.1f} ADR` · `{len(rested)} przejść`',
                f'**Przerwa poniżej 15 min:** `{mean(quick):+.1f} ADR` · `{len(quick)} przejść`',
                '-# Zmiana względem poprzedniego meczu; nie dowodzi, że przerwa poprawiła lub pogorszyła grę.',
            )))

    returns = [m for p, m in zip(rows, rows[1:]) if m.start - p.end >= 7 * 86400 and m.adr is not None]
    regular_starts = [g[0] for g in report.groups[1:] if g[0].adr is not None and g[0] not in returns]
    if len(returns) >= 8 and len(regular_starts) >= 20:
        cards.append(Pattern('return', 'Pierwsze mecze po tygodniu bez gry na Faceicie', (
            compare_line('Powroty po co najmniej 7 dniach', returns), compare_line('Pozostałe początki sesji', regular_starts),
            '-# Nie wiemy, czy w przerwie gracz trenował lub grał poza Faceitem.',
        ), True))

    streaks = {}
    for group in report.groups[1:]:
        count = 0
        previous_win = None
        for m in group:
            streaks[m.id] = (previous_win, count)
            count = count + 1 if m.win == previous_win else 1
            previous_win = m.win
    for win, key, label in [(False, 'loss_streak', 'porażkach'), (True, 'win_streak', 'wygranych')]:
        eligible = [m for m in continued if streaks[m.id][0] is win]
        result = rate_contrast(eligible, lambda m: streaks[m.id][1] >= 2, minimum=25, effect=12)
        if result:
            one, several, _ = result
            cards.append(Pattern(key, f'Następny mecz po kolejnych {label}', (
                f'**Po dwóch lub więcej:** `{fmt(winrate(several))}% WR` · `{len(several)} meczów`',
                f'**Po jednej:** `{fmt(winrate(one))}% WR` · `{len(one)} meczów`',
                '-# Serie liczymy wyłącznie wewnątrz sesji, nie przez wielodniowe przerwy.',
            )))

    if len(stats) >= 80:
        sorted_adr = sorted(m.adr for m in stats)
        quartile = sorted_adr[len(sorted_adr) // 4]
        starts = [group for group in report.groups[1:] if len(group) >= 2 and group[0].adr is not None]
        cold = [g[1] for g in starts if g[0].adr <= quartile]
        other = [g[1] for g in starts if g[0].adr > quartile]
        if min(len(cold), len(other)) >= 20 and abs(winrate(cold) - winrate(other)) >= 15:
            combined = sorted(cold + other, key=lambda m: m.start)
            ids = {m.id for m in cold}
            if stable_contrast(combined, lambda m: m.id in ids, False, True, minimum=6):
                cards.append(Pattern('cold_start', 'Drugi mecz po słabym początku sesji', (
                    f'**Po pierwszym z ADR ≤ {quartile:.1f}:** `{fmt(winrate(cold))}% WR` · `{len(cold)} meczów`',
                    f'**Po pozostałych początkach:** `{fmt(winrate(other))}% WR` · `{len(other)} meczów`',
                    '-# Słaby początek to dolna ćwiartka własnego ADR, nie arbitralny próg dla wszystkich.',
                )))

    if len(stats) >= 100:
        older, newer = stats[-100:-50], stats[-50:]
        old_values, new_values = [m.adr for m in older], [m.adr for m in newer]
        old_sd, new_sd = stdev(old_values), stdev(new_values)
        if old_sd > 0 and (new_sd / old_sd >= 1.3 or new_sd / old_sd <= .77):
            direction = 1 if new_sd > old_sd else -1
            if all((stdev(new_values[i:i + 25]) - stdev(old_values[i:i + 25])) * direction > 0 for i in (0, 25)):
                cards.append(Pattern('variability', 'Zmienia się regularność osobistych wyników', (
                    f'**Rozrzut ADR, poprzednie 50:** `{old_sd:.1f}` · **Ostatnie 50:** `{new_sd:.1f}`',
                    f'**Średnie ADR:** `{mean(old_values):.1f} → {mean(new_values):.1f}`',
                    '-# Rozrzut to odchylenie standardowe. Mniejszy rozrzut nie musi oznaczać lepszej gry.',
                ), True))
        baseline = sorted(old_values)[len(old_values) // 4]
        old_low = sum(v <= baseline for v in old_values)
        new_low = sum(v <= baseline for v in new_values)
        if abs(new_low - old_low) >= 8:
            direction = 1 if new_low > old_low else -1
            if all((sum(v <= baseline for v in new_values[i:i + 25]) - sum(v <= baseline for v in old_values[i:i + 25])) * direction > 0 for i in (0, 25)):
                cards.append(Pattern('low_games', 'Zmienia się liczba meczów poniżej własnej normy', (
                    f'**ADR ≤ {baseline:.1f}:** `{old_low}/50 → {new_low}/50 meczów`',
                    '-# Próg wyznacza dolna ćwiartka poprzednich 50 meczów; sprawdzamy go na kolejnych 50.',
                ), True))
        hs_rows = [m for m in stats if m.hs is not None and m.kr is not None]
        if len(hs_rows) >= 100:
            old, new = hs_rows[-100:-50], hs_rows[-50:]
            delta = average(new, 'hs') - average(old, 'hs')
            if abs(delta) >= 8 and abs(average(new, 'kr') - average(old, 'kr')) <= .04 and all(
                    (average(new[i:i + 25], 'hs') - average(old[i:i + 25], 'hs')) * delta > 0 for i in (0, 25)):
                cards.append(Pattern('hs_profile', 'Udział headshotów zmienia się przy podobnym K/R', (
                    f'**HS:** `{fmt(average(old, "hs"))}% → {fmt(average(new, "hs"))}%`',
                    f'**K/R:** `{fmt(average(old, "kr"), 2)} → {fmt(average(new, "kr"), 2)}`',
                    '-# Większy udział HS nie musi oznaczać lepszego celowania; może zmienić się dobór broni.',
                ), True))
        survival = [m for m in stats if m.deaths is not None and m.rounds and m.kr is not None]
        if len(survival) >= 100:
            old, new = survival[-100:-50], survival[-50:]
            old_dr, new_dr = mean(m.deaths / m.rounds for m in old), mean(m.deaths / m.rounds for m in new)
            delta = new_dr - old_dr
            if abs(delta) >= .05 and abs(average(new, 'kr') - average(old, 'kr')) <= .04 and all(
                    (mean(m.deaths / m.rounds for m in new[i:i + 25]) - mean(m.deaths / m.rounds for m in old[i:i + 25])) * delta > 0 for i in (0, 25)):
                cards.append(Pattern('survival', 'Częstotliwość śmierci zmienia się przy podobnym K/R', (
                    f'**Śmierci na rundę:** `{old_dr:.2f} → {new_dr:.2f}`',
                    f'**K/R:** `{fmt(average(old, "kr"), 2)} → {fmt(average(new, "kr"), 2)}`',
                    '-# Mniej śmierci nie dowodzi lepszego ustawienia ani większej wartości dla drużyny.',
                ), True))

    multis = [m for m in rows if all(getattr(m, key) is not None for key in ('doubles', 'triples', 'quads', 'pentas'))]
    if len(multis) >= 50:
        two = sum(m.doubles for m in multis)
        three = sum(m.triples + m.quads + m.pentas for m in multis)
        games = sum(m.triples + m.quads + m.pentas > 0 for m in multis)
        cards.append(Pattern('multikills', 'Rundy z kilkoma fragami', (
            f'**Rundy 2K:** `{int(two)}` · **Rundy 3K+:** `{int(three)}`',
            f'**Mecze z co najmniej jednym 3K+:** `{games}/{len(multis)}`',
            '-# 3K+ oznacza trzy lub więcej fragów w jednej rundzie, nie serię w całym meczu.',
        ), True))

    scored = [m for m in stats if m.own_score is not None and m.opponent_score is not None and
              max(m.own_score, m.opponent_score) >= 13 and m.own_score != m.opponent_score]
    result = numeric_contrast(scored, lambda m: max(m.own_score, m.opponent_score) > 13, minimum=20, effect=7)
    if result:
        regulation, extra, _ = result
        cards.append(Pattern('overtime_adr', 'ADR w meczach z dogrywką', (
            compare_line('Z dogrywką', extra), compare_line('Bez dogrywki', regulation),
            '-# ADR dotyczy całego meczu, nie samych dodatkowych rund.',
        )))
    lead = [m for m in rows if m.half is not None and m.half >= 8]
    if len(lead) >= 30:
        losses = sum(not m.win for m in lead)
        cards.append(Pattern('lead_conversion', 'Utrzymywanie wyraźnego prowadzenia po połowie', (
            f'**Przy prowadzeniu ≥ 8:4:** `{fmt(winrate(lead))}% WR` · `{len(lead)} meczów`',
            f'**Mecze zakończone porażką:** `{losses}/{len(lead)}`',
            '-# To własna częstość w tej próbie, bez oceny względem innych graczy.',
        ), True))

    mapped = [m for m in rows if m.map]
    if len(mapped) >= 100:
        older, newer = mapped[-100:-50], mapped[-50:]
        old_counts, new_counts = Counter(m.map for m in older), Counter(m.map for m in newer)
        old_name, old_n = old_counts.most_common(1)[0]
        new_name, new_n = new_counts.most_common(1)[0]
        if old_name != new_name or abs(new_counts[old_name] - old_n) >= 10:
            cards.append(Pattern('map_pool', 'Zmienia się rozkład granych map', (
                f'**Poprzednie 50:** najczęściej {escaped(old_name.removeprefix("de_"))} · `{old_n}/50`',
                f'**Ostatnie 50:** najczęściej {escaped(new_name.removeprefix("de_"))} · `{new_n}/50`',
                '-# Rozkład map może tłumaczyć część zmian ogólnych statystyk. Nie znamy głosowań veto.',
            ), True))

    opponents = Counter(uid for m in rows for uid in m.opponents)
    if opponents:
        uid, count = opponents.most_common(1)[0]
        if count >= 10:
            encounters = [m for m in rows if uid in m.opponents]
            nick = escaped(encounters[-1].opponents[uid])
            cards.append(Pattern('rival', f'Powtarzający się przeciwnik: {nick}', (
                f'**Przeciw tej osobie:** `{fmt(winrate(encounters))}% WR` · `{len(encounters)} meczów`',
                f'**W tych spotkaniach:** `{fmt(average(encounters, "adr"))} ADR`',
                '-# To wyniki drużyny przeciw drużynie z tą osobą, nie bezpośrednich pojedynków.',
            ), True))

    assert len({card.key for card in cards}) == len(cards)
    return cards
