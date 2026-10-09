"""Persistent publication settings for scheduled FACEIT summaries."""
import json
from pathlib import Path

CONFIG_FILE = Path('txt/faceit_weekly_config.json')
DEFAULT_CONFIG = {'channel_id': 1301248598108798996, 'interval_weeks': 1}


def load_config():
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    with CONFIG_FILE.open(encoding='utf-8') as file:
        return {**DEFAULT_CONFIG, **json.load(file)}


def update_config(**changes):
    config = load_config()
    config.update(changes)
    if type(config['interval_weeks']) is not int or config['interval_weeks'] not in (1, 2):
        raise ValueError('Wybierz wysyłkę co tydzień lub co dwa tygodnie.')
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as file:
        json.dump(config, file, ensure_ascii=False, indent=4)
    temporary.replace(CONFIG_FILE)
