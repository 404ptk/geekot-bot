"""Prosty podgląd wyniku meczu FACEIT w terminalu."""

import re
import time
from datetime import datetime

import requests


POLL_INTERVAL_SECONDS = 60
MATCH_ID_PATTERN = re.compile(r"1-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
MATCH_URL = "https://www.faceit.com/api/match/v4/match/{match_id}"


def extract_match_id(value: str) -> str | None:
    """Akceptuj samo ID albo pełny link do pokoju FACEIT."""
    match = MATCH_ID_PATTERN.search(value.strip())
    return match.group(0) if match else None


def get_match_data(session: requests.Session, match_id: str) -> dict:
    response = session.get(MATCH_URL.format(match_id=match_id), timeout=15)
    response.raise_for_status()
    body = response.json()
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Odpowiedź FACEIT nie zawiera danych meczu.")
    if payload.get("id") != match_id:
        raise ValueError("FACEIT zwrócił dane innego meczu.")

    teams = payload.get("teams") or {}
    factions = payload.get("summaryResults", {}).get("factions") or {}
    # Niektóre odpowiedzi mogą mieć wynik wyłącznie w tablicy results.
    if not factions:
        results = payload.get("results") or []
        if results:
            factions = results[-1].get("factions") or {}

    team1 = teams.get("faction1", {}).get("name", "Drużyna 1")
    team2 = teams.get("faction2", {}).get("name", "Drużyna 2")
    score1 = factions.get("faction1", {}).get("score")
    score2 = factions.get("faction2", {}).get("score")
    if score1 is None or score2 is None:
        raise ValueError("Mecz został znaleziony, ale wynik nie jest jeszcze dostępny.")

    return {
        "team1": team1,
        "team2": team2,
        "score1": score1,
        "score2": score2,
        "status": payload.get("status", payload.get("state", "nieznany")),
    }


def main() -> None:
    entered_id = input("Podaj ID meczu FACEIT (np. 1-...): ")
    match_id = extract_match_id(entered_id)
    if not match_id:
        print("Nieprawidłowe ID. Oczekuję ID w formacie 1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": "FaceitScoreScout/1.0", "Accept": "application/json"})
    try:
        data = get_match_data(session, match_id)
    except requests.RequestException as exc:
        print(f"Nie udało się pobrać meczu: {exc}")
        return
    except (ValueError, requests.JSONDecodeError) as exc:
        print(f"Nie udało się odczytać meczu: {exc}")
        return

    print(f"Mecz znaleziony: {data['team1']} vs {data['team2']} (status: {data['status']})")
    print("Śledzenie wyniku co 60 sekund. Zakończ przez Ctrl+C.\n")
    previous_score = None

    try:
        while True:
            try:
                data = get_match_data(session, match_id)
                score = (data["score1"], data["score2"])
                changed = previous_score is not None and score != previous_score
                marker = "  ← wynik się zmienił" if changed else ""
                now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                print(
                    f"[{now}] {data['team1']} {score[0]} : {score[1]} {data['team2']}"
                    f" | {data['status']}{marker}",
                    flush=True,
                )
                previous_score = score
            except requests.RequestException as exc:
                print(f"Błąd połączenia: {exc}", flush=True)
            except ValueError as exc:
                print(f"Błąd odczytu: {exc}", flush=True)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nZakończono śledzenie.")


if __name__ == "__main__":
    main()
