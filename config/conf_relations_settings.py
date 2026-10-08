"""Default nicknames, inflections and persistent relation-user configuration."""
import json
import os
from pathlib import Path
from typing import Dict


RELATION_USERS_FILE = "txt/relation_users.json"



ALLOWED_USERS = [
    "jaro",
    "mateuko",
    "radzio",
    "kuzia",
    "hubi",
    "plaster",
    "masny",
    "kajtek",
]



USER_DATIVE_FORMS = {
    "jaro": "jarowi",
    "mateuko": "mateukowi",
    "radzio": "radziowi",
    "kuzia": "kuzi",
    "hubi": "hubiemu",
    "plaster": "plastrowi",
    "masny": "masnemu",
    "kajtek": "kajtkowi",
}



USER_INSTRUMENTAL_FORMS = {
    "jaro": "jarem",
    "mateuko": "mateukiem",
    "radzio": "radziem",
    "kuzia": "kuzią",
    "hubi": "hubim",
    "plaster": "plastrem",
    "masny": "masnym",
    "kajtek": "kajtkiem",
}



def load_relation_users() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(RELATION_USERS_FILE):
        return {
            nick: {"celownik": USER_DATIVE_FORMS[nick], "narzednik": USER_INSTRUMENTAL_FORMS[nick]}
            for nick in ALLOWED_USERS
        }
    with open(RELATION_USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)



def save_relation_users(users: Dict[str, Dict[str, str]]) -> None:
    Path(RELATION_USERS_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(RELATION_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=4, ensure_ascii=False)



def add_relation_user(nick: str, celownik: str, narzednik: str) -> str:
    nick = nick.strip().lower()
    celownik, narzednik = celownik.strip(), narzednik.strip()
    if (not nick or nick in (".", "..") or any(char in nick for char in "/\\|")
            or not celownik or not narzednik
            or any(len(value) > 100 for value in (nick, celownik, narzednik))):
        raise ValueError("Podaj nick i obie odmiany (1–100 znaków). Nick nie może zawierać /, \\ ani |.")
    users = load_relation_users()
    if nick in users:
        raise ValueError("Ten nick jest już na liście relacji.")
    users[nick] = {"celownik": celownik, "narzednik": narzednik}
    save_relation_users(users)
    return nick



def remove_relation_user(nick: str) -> None:
    from commands.relations import (
        ACTIVE_TEMP_TASKS, load_relations, load_temp_relations,
        save_relations, save_temp_relations,
    )

    nick = nick.strip().lower()
    users = load_relation_users()
    if nick not in users:
        raise ValueError("Tego nicku nie ma już na liście relacji.")
    data = load_relations()
    data.pop(nick, None)
    for other in list(data):
        data[other].pop(nick, None)
        if not data[other]:
            del data[other]
    temp_data = load_temp_relations()
    for pair_key, record in list(temp_data.items()):
        if nick in (record.get("user_a"), record.get("user_b")):
            task = ACTIVE_TEMP_TASKS.pop(pair_key, None)
            if task:
                task.cancel()
            del temp_data[pair_key]
    save_relations(data)
    save_temp_relations(temp_data)
    del users[nick]
    save_relation_users(users)

