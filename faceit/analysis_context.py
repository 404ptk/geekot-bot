"""Contextual comparisons that go beyond separate map or teammate totals."""
from collections import Counter, defaultdict
import math
from statistics import mean, stdev

from faceit.analysis import average, escaped, fmt, parse_matches, winrate
from faceit.analysis_patterns import Pattern, best


def correlation(left, right):
    if len(left) < 3:
        return None
    a = [v - mean(left) for v in left]
    b = [v - mean(right) for v in right]
    denominator = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    return sum(x * y for x, y in zip(a, b)) / denominator if denominator else None


def weighted(values):
    total = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / total


def supported_rate_difference(rows, classify, outcome, minimum=30, effect=15):
    left = [m for m in rows if not classify(m)]
    right = [m for m in rows if classify(m)]
    if min(len(left), len(right)) < minimum:
        return None
    a, b = mean(outcome(m) for m in left), mean(outcome(m) for m in right)
    delta = b - a
    radius = 1.96 * math.sqrt(a * (1 - a) / len(left) + b * (1 - b) / len(right))
    if abs(delta) * 100 < effect or abs(delta) <= radius:
        return None
    for half in (rows[:len(rows) // 2], rows[len(rows) // 2:]):
        x = [m for m in half if not classify(m)]
        y = [m for m in half if classify(m)]
        if min(len(x), len(y)) < 10 or (mean(outcome(m) for m in y) - mean(outcome(m) for m in x)) * delta <= 0:
            return None
    return left, right, a * 100, b * 100


def collect_context_patterns(report):
    rows = report.matches
    stats = [m for m in rows if m.adr is not None]
    cards = []
    mates = Counter(uid for m in stats for uid in m.mates).most_common(6)
    strata = defaultdict(list)
    for m in stats:
        if m.map:
            strata[(m.date.strftime('%Y-%m'), m.map)].append(m)
    candidates = []
    for uid, _ in mates:
        contrasts = []
        with_count = without_count = 0
        for key, group in sorted(strata.items()):
            together = [m for m in group if uid in m.mates]
            apart = [m for m in group if uid not in m.mates]
            if min(len(together), len(apart)) >= 3:
                contrasts.append((average(together, 'adr') - average(apart, 'adr'), min(len(together), len(apart))))
                with_count += len(together)
                without_count += len(apart)
        # Weight by the smaller group, rather than by the dominant lineup.
        if len(contrasts) < 8 or sum(w for _, w in contrasts) < 40:
            continue
        delta = weighted(contrasts)
        total = sum(w for _, w in contrasts)
        effective = total * total / sum(w * w for _, w in contrasts)
        variance = sum(w * (value - delta) ** 2 for value, w in contrasts) / total
        radius = 1.96 * math.sqrt(variance / effective)
        parts = (contrasts[:len(contrasts) // 2], contrasts[len(contrasts) // 2:])
        if abs(delta) < 5 or abs(delta) <= radius or not all(weighted(part) * delta > 0 for part in parts):
            continue
        together = [m for m in stats if uid in m.mates]
        apart = [m for m in stats if uid not in m.mates]
        raw = average(together, 'adr') - average(apart, 'adr')
        nick = escaped(together[-1].mates[uid])
        candidates.append((abs(delta), Pattern('teammate_matched', f'Z {nick} różnica zostaje po uwzględnieniu map i miesięcy', (
            f'**Różnica ADR z tą osobą:** `{raw:+.1f}` w całej próbie · `{delta:+.1f}` w porównywalnych grupach',
            f'**Próby:** `{with_count}` z tą osobą / `{without_count}` bez · `{len(contrasts)}` grup mapa–miesiąc',
            '-# Porównujemy średnie z tą osobą i bez niej w tych samych mapach i miesiącach; wagą jest liczebność mniejszej grupy.',
            '-# Kierunek utrzymuje się w obu częściach grup. Pozostały skład i poziom przeciwników nadal nie są kontrolowane.',
        ))))
    cards.extend(best(candidates))

    # Exclude the latest match: its continuation cannot yet be observed.
    ended_ids = {group[-1].id for group in report.groups[:-1]}
    eligible = rows[:-1]
    result = supported_rate_difference(eligible, lambda m: m.win, lambda m: m.id in ended_ids, minimum=50, effect=15)
    if result:
        losses, wins, after_loss, after_win = result
        cards.append(Pattern('stop_result', 'Moment kończenia sesji wiąże się z ostatnim wynikiem', (
            f'**Sesja kończy się po porażce:** `{after_loss:.1f}%` · `{len(losses)} meczów`',
            f'**Po wygranej:** `{after_win:.1f}%` · `{len(wins)} meczów`',
            '-# Koniec sesji oznacza brak kolejnego meczu CS2 5v5 w ciągu 90 minut; nie wiemy, czy gracz przestał grać w ogóle.',
        )))
    if len(stats) >= 80:
        median = sorted(m.adr for m in stats)[len(stats) // 2]
        losses = [m for m in eligible if not m.win and m.adr is not None]
        result = supported_rate_difference(losses, lambda m: m.adr >= median, lambda m: m.id not in ended_ids)
        if result:
            low, high, low_rate, high_rate = result
            cards.append(Pattern('continue_loss_adr', 'Kontynuowanie po porażce zależy od własnego ADR w tej próbie', (
                f'**Po porażce z ADR < {median:.1f}:** kolejny mecz w sesji w `{low_rate:.1f}%` przypadków · `{len(low)} meczów`',
                f'**Z ADR ≥ {median:.1f}:** `{high_rate:.1f}%` · `{len(high)} meczów`',
                '-# Próg to własna mediana ADR. Nie znamy powodów kontynuowania lub kończenia sesji.',
            )))

    scored = [m for m in rows if m.own_score is not None and m.opponent_score is not None
              and max(m.own_score, m.opponent_score) >= 13 and m.own_score != m.opponent_score]
    if len(scored) >= 100:
        old, new = scored[-100:-50], scored[-50:]
        close = lambda m: min(m.own_score, m.opponent_score) >= 10
        old_n, new_n = sum(close(m) for m in old), sum(close(m) for m in new)
        delta = new_n - old_n
        if abs(delta) >= 10 and all((sum(close(m) for m in new[i:i + 25]) - sum(close(m) for m in old[i:i + 25])) * delta > 0 for i in (0, 25)):
            cards.append(Pattern('close_frequency', 'Zmienia się częstość meczów rozstrzyganych na styku', (
                f'**Bliskie wyniki w poprzednich 50:** `{old_n}/50` · **W ostatnich 50:** `{new_n}/50`',
                f'**WR tych dwóch bloków:** `{fmt(winrate(old))}% → {fmt(winrate(new))}%`',
                '-# Bliskie wyniki to 13:10–13:11 i dogrywki. Porównanie obejmuje mecze z wiarygodnym końcowym wynikiem.',
            ), True))

    assists = [m for m in stats if m.assists is not None and m.rounds and m.kr is not None]
    if len(assists) >= 100:
        old, new = assists[-100:-50], assists[-50:]
        ar = lambda group: mean(m.assists / m.rounds for m in group)
        delta = ar(new) - ar(old)
        if abs(delta) >= .04 and abs(average(new, 'kr') - average(old, 'kr')) <= .04 and all(
                (ar(new[i:i + 25]) - ar(old[i:i + 25])) * delta > 0 for i in (0, 25)):
            cards.append(Pattern('assist_profile', 'Asysty zmieniają się przy podobnej liczbie fragów na rundę', (
                f'**Asysty na rundę:** `{ar(old):.2f} → {ar(new):.2f}`',
                f'**K/R:** `{fmt(average(old, "kr"), 2)} → {fmt(average(new, "kr"), 2)}` · dwa bloki po 50 meczów',
                '-# Statystyka asyst nie opisuje całej współpracy ani jakości granatów.',
            ), True))

    multi = [m for m in stats if m.rounds and m.kr is not None and all(getattr(m, key) is not None for key in ('triples', 'quads', 'pentas'))]
    if len(multi) >= 100:
        old, new = multi[-100:-50], multi[-50:]
        events = lambda group: sum(m.triples + m.quads + m.pentas for m in group)
        rate = lambda group: 100 * events(group) / sum(m.rounds for m in group)
        old_rate, new_rate = rate(old), rate(new)
        if min(events(old), events(new)) >= 10 and min(old_rate, new_rate) > 0:
            delta = new_rate - old_rate
            ratio = max(old_rate, new_rate) / min(old_rate, new_rate)
            if ratio >= 1.5 and abs(average(new, 'kr') - average(old, 'kr')) <= .04 and all(
                    (rate(new[i:i + 25]) - rate(old[i:i + 25])) * delta > 0 for i in (0, 25)):
                cards.append(Pattern('multikill_profile', 'Rundy 3K+ zmieniają się przy podobnym K/R', (
                    f'**3K+ na 100 rund:** `{old_rate:.2f} → {new_rate:.2f}`',
                    f'**K/R:** `{fmt(average(old, "kr"), 2)} → {fmt(average(new, "kr"), 2)}` · dwa bloki po 50 meczów',
                    '-# Częstotliwość uwzględnia długość meczów. To rozkład fragów między rundami, nie ocena ich znaczenia.',
                ), True))

    if len(stats) >= 200:
        old, new = stats[-200:-100], stats[-100:]
        before, after = defaultdict(list), defaultdict(list)
        for m in old:
            if m.map:
                before[m.map].append(m)
        for m in new:
            if m.map:
                after[m.map].append(m)
        common = sorted(name for name in before.keys() & after.keys() if min(len(before[name]), len(after[name])) >= 10)
        if len(common) >= 3:
            weights = {name: len(before[name]) + len(after[name]) for name in common}
            deltas = [(average(after[name], 'adr') - average(before[name], 'adr'), weights[name]) for name in common]
            adjusted = weighted(deltas)
            raw = average(new, 'adr') - average(old, 'adr')
            old_common = [m for m in old if m.map in common]
            new_common = [m for m in new if m.map in common]
            common_raw = average(new_common, 'adr') - average(old_common, 'adr')
            # Show this contextual card only when adjusting the map mixture
            # materially changes the apparent trend.
            if abs(adjusted) >= 6 and abs(adjusted - raw) >= 3:
                cards.append(Pattern('map_adjusted_trend', 'Wybór i proporcje map zmieniają obraz trendu ADR', (
                    f'**Zmiana w dwóch blokach po 100:** `{raw:+.1f} ADR`',
                    f'**Tylko wspólne mapy:** `{common_raw:+.1f} ADR` · `{len(old_common)}/{len(new_common)} meczów`',
                    f'**Identyczne wagi tych map:** `{adjusted:+.1f} ADR` · `{len(common)} mapy`',
                    '-# Uwzględniamy mapy z co najmniej 10 meczami w obu blokach i identycznymi wagami po obu stronach.',
                    '-# To opisowe przeliczenie, nie kontrola składów ani poziomu przeciwników. Nie obejmuje map z małą próbą.',
                ), True))

    own_id = report.player['player_id']
    for dataset in report.peer_datasets[:1]:
        peer_rows, _ = parse_matches(dataset)
        by_id = {m.id: m for m in peer_rows if own_id in m.mates and m.adr is not None}
        peer_id = dataset['player']['player_id']
        pairs = [(m, by_id[m.id]) for m in stats if m.id in by_id and peer_id in m.mates and m.win == by_id[m.id].win]
        if len(pairs) < 30:
            continue
        groups = [[pair for pair in pairs if pair[0].win == win] for win in (False, True)]
        if any(len(group) < 10 for group in groups):
            continue
        per_outcome = [correlation([a.adr for a, b in group], [b.adr for a, b in group]) for group in groups]
        if any(value is None for value in per_outcome) or per_outcome[0] * per_outcome[1] <= 0:
            continue
        # Remove the average win/loss difference before comparing the duet.
        baselines = {win: (mean(a.adr for a, b in group), mean(b.adr for a, b in group))
                     for win, group in zip((False, True), groups)}
        a_values = [a.adr - baselines[a.win][0] for a, b in pairs]
        b_values = [b.adr - baselines[a.win][1] for a, b in pairs]
        value = correlation(a_values, b_values)
        if value is None or not .4 <= abs(value) < 1:
            continue
        z = math.atanh(value)
        if abs(z) <= 1.96 / math.sqrt(len(pairs) - 3):
            continue
        mid = len(pairs) // 2
        halves = [correlation(a_values[:mid], b_values[:mid]), correlation(a_values[mid:], b_values[mid:])]
        if any(part is None or part * value <= 0 for part in halves):
            continue
        nick = escaped(dataset['player']['nickname'])
        cards.append(Pattern('paired_correlation', f'Osobiste statystyki duetu z {nick} są powiązane', (
            f'**Korelacja ADR:** `{value:+.2f}` · `{len(pairs)} wspólnych meczów`',
            f'**W wygranych:** `{per_outcome[1]:+.2f}` · **W porażkach:** `{per_outcome[0]:+.2f}`',
            '-# Korelacja główna odejmuje średnią różnicę między wygranymi i porażkami. Dodatnia oznacza zbieżny ruch, ujemna przeciwny.',
            '-# Nie dowodzi synergii, zmiany ról ani odbierania sobie fragów.',
        )))
    return cards
