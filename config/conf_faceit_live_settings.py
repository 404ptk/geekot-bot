"""Persistent settings used only by the FACEIT LIVE watcher."""
import json
from pathlib import Path

CONFIG_FILE = Path('txt/faceit_live_config.json')
DEFAULT_CHANNEL_ID = 1504791638264905778
DEFAULT_PLAYERS = ['utopiasz', 'radzioswir', 'PhesterM9', '-Masny-', '-mateuko', 'Kvzia', 'Kajetov', 'MlodyHubii']


def load_config():
    if not CONFIG_FILE.exists():
        return {'players': list(DEFAULT_PLAYERS), 'channel_id': DEFAULT_CHANNEL_ID}
    with CONFIG_FILE.open(encoding='utf-8') as file:
        return json.load(file)


def save_config(config):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as file:
        json.dump(config, file, ensure_ascii=False, indent=4)
    temporary.replace(CONFIG_FILE)


def add_player(nickname):
    nickname = nickname.strip()
    if not nickname or len(nickname) > 100 or any(not (char.isascii() and (char.isalnum() or char in '-_')) for char in nickname):
        raise ValueError('Podaj nick FACEIT: litery, cyfry, myślnik lub podkreślenie (1–100 znaków).')
    config = load_config()
    if nickname.lower() in {nick.lower() for nick in config['players']}:
        raise ValueError('Ten gracz jest już śledzony.')
    config['players'].append(nickname)
    save_config(config)
    return nickname


def remove_player(nickname):
    config = load_config()
    if nickname not in config['players']:
        raise ValueError('Ten gracz nie jest już śledzony.')
    config['players'].remove(nickname)
    save_config(config)


def set_channel(channel_id):
    config = load_config()
    config['channel_id'] = int(channel_id)
    save_config(config)
