"""Validated, persistent custom word dictionaries for Impostor."""
import json
from pathlib import Path
import time
import unicodedata
import uuid

DICTIONARY_FILE = Path(__file__).resolve().parents[2] / 'data' / 'impostor' / 'dictionaries.json'
STANDARD_DICTIONARY_FILE = Path(__file__).resolve().with_name('standard_dictionary.json')
INTERNET_DICTIONARY_FILE = Path(__file__).resolve().with_name('polish_internet_dictionary.json')
MAX_WORDS = 200
MAX_DICTIONARIES = 100


def normalize_text(value):
    value = ' '.join(unicodedata.normalize('NFKC', value).split())
    if any(unicodedata.category(char).startswith('C') for char in value):
        raise ValueError('Usuń niewidoczne znaki z wpisanego tekstu.')
    return value


def parse_words(text):
    words, seen = [], set()
    text = unicodedata.normalize('NFKC', text)
    for index, entry in enumerate(text.split(','), start=1):
        word = normalize_text(entry)
        if not word or word.strip('.') == '':
            continue
        if len(word) > 64:
            raise ValueError(f'Słowo lub hasło nr {index} ma więcej niż 64 znaki.')
        if not any(char.isalnum() for char in word):
            raise ValueError(f'Hasło nr {index} musi zawierać literę lub cyfrę.')
        key = word.casefold()
        if key not in seen:
            seen.add(key)
            words.append(word)
        if len(words) > MAX_WORDS:
            raise ValueError(f'Słownik może zawierać maksymalnie {MAX_WORDS} różnych haseł.')
    if len(words) < 2:
        raise ValueError('Podaj co najmniej 2 różne słowa lub hasła, oddzielone przecinkami.')
    return words


def count_words_without_hints(words):
    """Count entries without a non-empty clue in the ``word:hint`` format."""
    count = 0
    for entry in words:
        if not isinstance(entry, str):
            count += 1
            continue
        word, separator, hint = entry.partition(':')
        if not separator or not word.strip() or not hint.strip():
            count += 1
    return count


def load_standard_dictionary():
    """Load the bundled standard dictionary; it is separate from guild libraries."""
    try:
        data = json.loads(STANDARD_DICTIONARY_FILE.read_text(encoding='utf-8'))
        words = data['words']
        if data.get('version') != 1 or not isinstance(words, list) or not words:
            raise ValueError
        for entry in words:
            if (not isinstance(entry, dict) or not isinstance(entry.get('word'), str)
                    or not entry['word'].strip() or not isinstance(entry.get('hints'), list)
                    or len(entry['hints']) != 2
                    or any(not isinstance(hint, str) or not hint.strip() for hint in entry['hints'])):
                raise ValueError
        return words
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError('Wbudowany słownik Impostora jest nieprawidłowy.') from error


def load_internet_dictionary():
    """Load the bundled Polish internet culture dictionary."""
    try:
        data = json.loads(INTERNET_DICTIONARY_FILE.read_text(encoding='utf-8'))
        words = data['words']
        if data.get('version') != 1 or not isinstance(words, list) or len(words) != 200:
            raise ValueError
        for entry in words:
            if (not isinstance(entry, dict) or not isinstance(entry.get('word'), str)
                    or not entry['word'].strip() or not isinstance(entry.get('hints'), list)
                    or len(entry['hints']) not in (1, 2)
                    or any(not isinstance(hint, str) or not hint.strip() for hint in entry['hints'])):
                raise ValueError
        return words
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError('Wbudowany słownik polskiego internetu jest nieprawidłowy.') from error


def load_store():
    if not DICTIONARY_FILE.exists():
        return {'version': 1, 'guilds': {}}
    try:
        data = json.loads(DICTIONARY_FILE.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('guilds'), dict):
            raise ValueError
        for records in data['guilds'].values():
            if not isinstance(records, list):
                raise ValueError
            for record in records:
                if (not isinstance(record, dict) or not isinstance(record.get('id'), str)
                        or not isinstance(record.get('name'), str) or not 2 <= len(record['name']) <= 60
                        or not isinstance(record.get('owner_id'), int)
                        or not isinstance(record.get('words'), list) or not 2 <= len(record['words']) <= MAX_WORDS
                        or any(not isinstance(word, str) or not word or len(word) > 64 for word in record['words'])):
                    raise ValueError
        return data
    except (json.JSONDecodeError, ValueError, TypeError) as error:
        raise ValueError('Plik słowników jest nieprawidłowy. Zapis został przerwany; zgłoś to administratorowi.') from error


def list_dictionaries(guild_id):
    return load_store()['guilds'].get(str(guild_id), [])


def create_dictionary(guild_id, owner_id, name, text):
    name = normalize_text(name)
    if not 2 <= len(name) <= 60:
        raise ValueError('Nazwa słownika musi mieć od 2 do 60 znaków.')
    words = parse_words(text)
    data = load_store()
    records = data['guilds'].setdefault(str(guild_id), [])
    if len(records) >= MAX_DICTIONARIES:
        raise ValueError(f'Na serwerze może być maksymalnie {MAX_DICTIONARIES} własnych słowników.')
    if any(normalize_text(record['name']).casefold() == name.casefold() for record in records):
        raise ValueError('Słownik o tej nazwie już istnieje na serwerze. Wybierz inną nazwę.')
    record = {'id': uuid.uuid4().hex, 'name': name, 'words': words,
              'owner_id': owner_id, 'created_at': int(time.time())}
    records.append(record)
    DICTIONARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DICTIONARY_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(DICTIONARY_FILE)
    return record


def update_dictionary(guild_id, owner_id, dictionary_id, name, text):
    name = normalize_text(name)
    if not 2 <= len(name) <= 60:
        raise ValueError('Nazwa słownika musi mieć od 2 do 60 znaków.')
    words = parse_words(text)
    data = load_store()
    records = data['guilds'].get(str(guild_id), [])
    record = next((item for item in records if item['id'] == dictionary_id), None)
    if record is None or record['owner_id'] != owner_id:
        raise ValueError('Nie znaleziono słownika, który możesz edytować.')
    if any(item['id'] != dictionary_id and normalize_text(item['name']).casefold() == name.casefold()
           for item in records):
        raise ValueError('Słownik o tej nazwie już istnieje na serwerze. Wybierz inną nazwę.')
    record['name'] = name
    record['words'] = words
    DICTIONARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DICTIONARY_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(DICTIONARY_FILE)
    return record


def delete_dictionary(guild_id, owner_id, dictionary_id):
    data = load_store()
    guild_key = str(guild_id)
    records = data['guilds'].get(guild_key, [])
    index = next((i for i, item in enumerate(records) if item['id'] == dictionary_id), None)
    if index is None or records[index]['owner_id'] != owner_id:
        raise ValueError('Nie znaleziono słownika, który możesz usunąć.')
    record = records.pop(index)
    if not records:
        data['guilds'].pop(guild_key, None)
    DICTIONARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DICTIONARY_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(DICTIONARY_FILE)
    return record
