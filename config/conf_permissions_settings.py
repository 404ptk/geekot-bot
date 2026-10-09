"""Guild-scoped moderator thresholds; command overrides replace the global rule."""
import json
from pathlib import Path

CONFIG_FILE = Path('txt/permissions_config.json')
MODERATOR_COMMANDS = {
    'czysc': 'Usuwanie wiadomości z kanału',
    'otworz': 'Przywracanie kanału z archiwum',
    'zamknij': 'Przenoszenie kanału do archiwum i blokowanie pisania',
}


def load_config(guild_id):
    if not CONFIG_FILE.exists():
        return {'moderator_role_id': None, 'commands': {}}
    with CONFIG_FILE.open(encoding='utf-8') as file:
        data = json.load(file).get(str(guild_id), {})
    return {'moderator_role_id': data.get('moderator_role_id'), 'commands': dict(data.get('commands', {}))}


def set_role(guild_id, role_id, command=None, inherit=False):
    if command is not None and command not in MODERATOR_COMMANDS:
        raise ValueError('Nieznana komenda moderatora.')
    data = {}
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open(encoding='utf-8') as file:
            data = json.load(file)
    config = load_config(guild_id)
    if command is None:
        config['moderator_role_id'] = role_id
    elif inherit:
        config['commands'].pop(command, None)
    else:
        config['commands'][command] = role_id
    data[str(guild_id)] = config
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
    temporary.replace(CONFIG_FILE)


def can_use_moderator_command(member, guild, command):
    if guild is None or command not in MODERATOR_COMMANDS:
        return False
    if member.guild_permissions.administrator:
        return True
    config = load_config(guild.id)
    role_id = config['commands'].get(command, config['moderator_role_id'])
    if role_id is None:
        return False
    role = guild.get_role(role_id)
    # A deleted threshold fails closed until an administrator chooses a new one.
    return role is not None and member.top_role >= role


async def require_moderator_command(interaction, command):
    if can_use_moderator_command(interaction.user, interaction.guild, command):
        return True
    await interaction.response.send_message(
        'Nie masz wystarczających uprawnień do wykonania tej komendy.', ephemeral=True)
    return False
