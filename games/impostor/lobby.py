"""Impostor lobby setup and joining through Components V2."""
import asyncio
import logging
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from games.impostor.dictionaries import (
    count_words_without_hints,
    create_dictionary,
    delete_dictionary,
    list_dictionaries,
    load_internet_dictionary,
    load_standard_dictionary,
    update_dictionary,
)

logger = logging.getLogger(__name__)
LOBBY_TIMEOUT = 600
LOBBY_MAX_DURATION = 900
LOGO_FILE = Path(__file__).resolve().with_name('logo.webp')
LOGO_FILENAME = 'impostor_logo.webp'
ROLE_IMAGE_FILES = {
    'impostor': ('impostor.webp', Path(__file__).resolve().with_name('impostor.webp')),
    'player': ('nieimpostor.webp', Path(__file__).resolve().with_name('nieimpostor.webp')),
}
STANDARD_DICTIONARY_ID = 'standard'
STANDARD_DICTIONARY_NAME = 'Zwyczajne'
INTERNET_DICTIONARY_ID = 'polish_internet'
INTERNET_DICTIONARY_NAME = 'Polski internet'
CUSTOM_DICTIONARIES_PER_PAGE = 22
# One open lobby per channel; separate channels can host separate groups.
LOBBIES = {}
ASSOCIATION_LOBBIES = {}
ASSOCIATION_LOGO = Path(__file__).resolve().parents[1] / "skojarzenia" / "skojarzenia.png"
_CREATE_LOCK = asyncio.Lock()


def impostor_count(players):
    return 1 if players <= 5 else 2 if players <= 8 else 3


def dictionary_names(selected, guild_id):
    selected = set(selected)
    records = list_dictionaries(guild_id)
    names = []
    if STANDARD_DICTIONARY_ID in selected:
        names.append(STANDARD_DICTIONARY_NAME)
    if INTERNET_DICTIONARY_ID in selected:
        names.append(INTERNET_DICTIONARY_NAME)
    names.extend(record['name'] for record in records if record['id'] in selected)
    if not names:
        return 'Brak'
    if len(names) > 5:
        return ', '.join(names[:5]) + f' i {len(names) - 5} innych'
    return ', '.join(names)


def logo_file(associations=False):
    return discord.File(ASSOCIATION_LOGO if associations else LOGO_FILE, filename=LOGO_FILENAME)


def role_file(is_impostor):
    role = 'impostor' if is_impostor else 'player'
    filename, path = ROLE_IMAGE_FILES[role]
    return discord.File(path, filename=filename)


def add_logo(container):
    container.add_item(discord.ui.MediaGallery(
        discord.MediaGalleryItem(f'attachment://{LOGO_FILENAME}', description='Impostor')
    ))


async def reply(interaction, text):
    kwargs = {'ephemeral': True, 'allowed_mentions': discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(text, **kwargs)
    else:
        await interaction.response.send_message(text, **kwargs)


class ImpostorView(discord.ui.LayoutView):
    async def on_error(self, interaction, error, item):
        logger.error('Impostor panel failed', exc_info=(type(error), error, error.__traceback__))
        try:
            await reply(interaction, 'Nie udało się zaktualizować panelu. Spróbuj ponownie.')
        except discord.HTTPException:
            logger.warning('Could not deliver impostor error response', exc_info=True)


class PlayerCard(ImpostorView):
    def __init__(self, is_impostor, word, hint, starter_id, end_at, associations=False):
        super().__init__(timeout=None)
        role = 'impostor' if is_impostor else 'player'
        filename = ROLE_IMAGE_FILES[role][0]
        title = '🎭 Jesteś IMPOSTOREM' if is_impostor else '🟢 Jesteś NIEIMPOSTOREM'
        detail = ('Twoja podpowiedź: **' + discord.utils.escape_markdown(hint) + '**'
                  if is_impostor and hint else
                  'Nie masz podpowiedzi — spróbuj odgadnąć hasło.' if is_impostor else
                  'Hasło: **' + discord.utils.escape_markdown(word) + '**')
        if associations:
            title = 'Nie znasz hasła' if is_impostor else 'Twoje hasło'
            detail = 'Odpowiadaj na pytania i spróbuj odgadnąć hasło.' if is_impostor else 'Hasło: **' + discord.utils.escape_markdown(word) + '**'
        container = discord.ui.Container(accent_color=0xE74C3C if is_impostor else 0x2ECC71)
        if not associations:
            container.add_item(discord.ui.MediaGallery(
                discord.MediaGalleryItem(f'attachment://{filename}', description=role)
            ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f'# {title}\n{detail}'))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f'⏱️ Czas do końca: <t:{end_at}:R>\n🎲 Zaczyna: <@{starter_id}>'
        ))
        self.add_item(container)


class Lobby:
    def __init__(self, guild_id, channel_id, owner_id, capacity, hints, duration=10,
                 dictionaries=(STANDARD_DICTIONARY_ID, INTERNET_DICTIONARY_ID),
                 owner_interaction=None, associations=False):
        if not 3 <= capacity <= 10:
            raise ValueError('Lobby musi mieć od 3 do 10 graczy.')
        available = {STANDARD_DICTIONARY_ID, INTERNET_DICTIONARY_ID} | {record['id'] for record in list_dictionaries(guild_id)}
        if not dictionaries or not set(dictionaries) <= available:
            raise ValueError('Wybierz przynajmniej jeden dostępny słownik.')
        self.associations = associations
        self.lobbies = ASSOCIATION_LOBBIES if associations else LOBBIES
        self.order = []
        self.key = (guild_id, channel_id)
        self.owner_id = owner_id
        self.capacity = capacity
        self.impostors = 1 if associations else impostor_count(capacity)
        self.hints = hints and not associations
        self.duration = duration
        self.dictionaries = tuple(key for key in [STANDARD_DICTIONARY_ID, INTERNET_DICTIONARY_ID,
                                                  *(record['id'] for record in list_dictionaries(guild_id))]
                                  if key in dictionaries)
        self.players = [owner_id]
        self.player_interactions = {owner_id: owner_interaction} if owner_interaction else {}
        self.roles = {}
        self.impostors_revealed = False
        self.word = None
        self.starter_id = None
        self.start_timestamp = None
        self.end_at = None
        self.state = 'waiting'
        self.message = None
        self.deadline = time.monotonic() + LOBBY_TIMEOUT
        self.hard_deadline = time.monotonic() + LOBBY_MAX_DURATION
        self.expiry_reason = None
        self.lock = asyncio.Lock()
        self.activity = asyncio.Event()
        self.timer = None
        self.game_timer = None
        self.view = LobbyView(self)

    def touch(self):
        self.deadline = time.monotonic() + LOBBY_TIMEOUT
        self.activity.set()

    def release(self):
        if self.lobbies.get(self.key) is self:
            del self.lobbies[self.key]
        if self.state != 'finished' or not self.roles or self.impostors_revealed:
            self.view.stop()
        for task in (self.timer, self.game_timer):
            if task and task is not asyncio.current_task():
                task.cancel()

    async def finish(self, state, expiry_reason=None):
        self.state = state
        self.expiry_reason = expiry_reason
        self.view.render()
        try:
            if self.message:
                await self.message.edit(view=self.view, allowed_mentions=discord.AllowedMentions.none())
        finally:
            self.release()

    def available_words(self):
        entries = []
        if STANDARD_DICTIONARY_ID in self.dictionaries:
            entries.extend(load_standard_dictionary())
        if INTERNET_DICTIONARY_ID in self.dictionaries:
            entries.extend(load_internet_dictionary())
        custom = {record['id']: record for record in list_dictionaries(self.key[0])}
        for dictionary_id in self.dictionaries:
            if dictionary_id in (STANDARD_DICTIONARY_ID, INTERNET_DICTIONARY_ID):
                continue
            record = custom.get(dictionary_id)
            if not record:
                continue
            for value in record['words']:
                if isinstance(value, str):
                    parts = value.split(':', 2)
                    entries.append({'word': parts[0].strip(),
                                    'hints': [part.strip() for part in parts[1:]]})
        entries = [entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get('word'), str)
                   and entry['word'].strip()]
        if not entries:
            raise ValueError('Wybrane słowniki nie zawierają dostępnych haseł.')
        return entries

    async def send_player_card(self, interaction, user_id):
        role = self.roles[user_id]
        view = PlayerCard(role['impostor'], self.word, role['hint'], self.starter_id, self.end_at, self.associations)
        await interaction.followup.send(
            view=view, ephemeral=True,
            **({} if self.associations else {'file': role_file(role['impostor'])}),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def begin_game(self, interaction):
        try:
            entries = self.available_words()
        except ValueError as error:
            await reply(interaction, str(error))
            return
        self.impostors = 1 if self.associations else impostor_count(len(self.players))
        chosen = random.choice(entries)
        self.word = chosen['word'].strip()
        hints = [hint.strip() for hint in chosen.get('hints', []) if isinstance(hint, str) and hint.strip()]
        clue = random.choice(hints) if self.hints and hints else ''
        impostor_ids = set(random.sample(self.players, self.impostors))
        self.roles = {
            user_id: {'impostor': user_id in impostor_ids,
                      'hint': clue if user_id in impostor_ids else ''}
            for user_id in self.players
        }
        if self.associations:
            self.order = random.sample(self.players, len(self.players))
            self.starter_id = self.order[0]
        else:
            self.starter_id = random.choice(self.players)
        self.start_timestamp = int(time.time())
        self.end_at = self.start_timestamp + self.duration * 60
        self.state = 'playing'
        self.activity.set()
        self.view.render()
        try:
            await self.message.edit(view=self.view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning('Could not update started impostor lobby', exc_info=True)

        deliveries = []
        for user_id in self.players:
            target = interaction if user_id == self.owner_id else self.player_interactions.get(user_id)
            if target is None:
                logger.warning('No interaction token for impostor player %s', user_id)
                continue
            try:
                await self.send_player_card(target, user_id)
                deliveries.append(user_id)
            except discord.HTTPException:
                logger.warning('Could not send private impostor card to %s', user_id, exc_info=True)
        self.game_timer = asyncio.create_task(self.end_game_at_deadline())
        failed = len(self.players) - len(deliveries)
        message = 'Gra wystartowała — wysłałem każdemu prywatną kartę.'
        if failed:
            message += f' Nie udało się wysłać {failed} kart; te osoby mogą kliknąć „Pokaż moją kartę” w lobby.'
        await reply(interaction, message)

    async def end_game_at_deadline(self):
        try:
            await asyncio.sleep(max(0, self.end_at - time.time()))
            async with self.lock:
                if self.state == 'playing':
                    self.state = 'finished'
                    self.view.render()
                    try:
                        await self.message.edit(view=self.view, allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        logger.warning('Could not update ended impostor game', exc_info=True)
                    self.release()
        except asyncio.CancelledError:
            raise

    async def watch_timeout(self):
        try:
            while self.state == 'waiting':
                self.activity.clear()
                remaining = max(0, min(self.deadline, self.hard_deadline) - time.monotonic())
                try:
                    await asyncio.wait_for(self.activity.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    async with self.lock:
                        now = time.monotonic()
                        if self.state == 'waiting' and now >= min(self.deadline, self.hard_deadline):
                            reason = 'max_duration' if now >= self.hard_deadline else 'inactivity'
                            await self.finish('expired', reason)
                            return
        except discord.HTTPException:
            logger.warning('Could not update expired impostor lobby', exc_info=True)

    async def action(self, interaction, action):
        if action in ('end_game', 'show_impostor'):
            if interaction.user.id != self.owner_id:
                await reply(interaction, 'Tylko twórca lobby może zakończyć grę lub pokazać impostorów.')
                return
            if self.state not in ('playing', 'finished') or not self.roles:
                await reply(interaction, 'Nie ma aktywnej gry do zakończenia ani impostorów do pokazania.')
                return
            if action == 'show_impostor':
                if self.impostors_revealed:
                    await reply(interaction, 'Impostorzy zostali już pokazani na czacie.')
                    return
                impostor_ids = [user_id for user_id, role in self.roles.items() if role['impostor']]
                names = ', '.join(f'<@{user_id}>' for user_id in impostor_ids)
                self.impostors_revealed = True
                await interaction.response.send_message(
                    (f'🧩 **Osoba bez hasła:** {names}' if self.associations else
                     f'🎭 **Impostor{"zy" if len(impostor_ids) > 1 else ""}:** {names}'),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self.view.render()
                try:
                    await self.message.edit(view=self.view, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    logger.warning('Could not update lobby after revealing impostors', exc_info=True)
                if self.state == 'finished':
                    self.view.stop()
                return
            await interaction.response.defer(ephemeral=True)
            async with self.lock:
                if self.state != 'playing':
                    await reply(interaction, 'Gra jest już zakończona.')
                    return
                await self.finish('finished')
                await reply(interaction, 'Gra zakończona.')
            return
        if action == 'reveal':
            if self.state != 'playing' or interaction.user.id not in self.roles:
                await reply(interaction, 'Nie masz aktywnej karty w tej grze.')
                return
            await interaction.response.defer(ephemeral=True)
            await self.send_player_card(interaction, interaction.user.id)
            return
        if action in ('start', 'cancel') and interaction.user.id != self.owner_id:
            await reply(interaction, 'Tylko twórca lobby może je rozpocząć lub anulować.')
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            if self.state != 'waiting' or self.lobbies.get(self.key) is not self:
                await reply(interaction, 'To lobby jest już zamknięte.')
                return
            now = time.monotonic()
            if now >= min(self.deadline, self.hard_deadline):
                reason = 'max_duration' if now >= self.hard_deadline else 'inactivity'
                await self.finish('expired', reason)
                message = ('Lobby wygasło po 15 minutach od utworzenia.' if reason == 'max_duration'
                           else 'Lobby wygasło po 10 minutach bezczynności.')
                await reply(interaction, message)
                return
            if action == 'join':
                if interaction.user.id in self.players:
                    await reply(interaction, 'Jesteś już w tym lobby.')
                    return
                if len(self.players) >= self.capacity:
                    await reply(interaction, 'Lobby jest pełne.')
                    return
                self.players.append(interaction.user.id)
                self.player_interactions[interaction.user.id] = interaction
                self.view.render()
                try:
                    await self.message.edit(view=self.view, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException as error:
                    self.players.remove(interaction.user.id)
                    self.player_interactions.pop(interaction.user.id, None)
                    if isinstance(error, discord.NotFound):
                        self.state = 'cancelled'
                        self.release()
                    self.view.render()
                    raise
                self.touch()
                await reply(interaction, f'Dołączono do lobby! {self.message.jump_url}')
            elif action == 'start':
                if len(self.players) < 3:
                    await reply(interaction, f'Do rozpoczęcia potrzeba co najmniej 3 graczy. Brakuje {3 - len(self.players)}.')
                    return
                await self.begin_game(interaction)
            elif action == 'cancel':
                await self.finish('cancelled')
                await reply(interaction, 'Lobby anulowane.')


class LobbyButton(discord.ui.Button):
    def __init__(self, lobby, action, label, style, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.lobby = lobby
        self.action_name = action

    async def callback(self, interaction):
        await self.lobby.action(interaction, self.action_name)


class LobbyView(ImpostorView):
    def __init__(self, lobby):
        # A separate activity timer ignores failed/unauthorized button clicks.
        super().__init__(timeout=None)
        self.lobby = lobby
        self.render()

    def render(self):
        self.clear_items()
        lobby = self.lobby
        active = lobby.state == 'waiting'
        full = len(lobby.players) == lobby.capacity
        shown_impostors = 1 if lobby.associations else (impostor_count(max(3, len(lobby.players))) if active else lobby.impostors)
        title, status = {
            'waiting': ('🎭 Lobby Impostora',
                        'Twórca może rozpocząć grę.' if len(lobby.players) >= 3 else 'Dołącz przyciskiem poniżej. Potrzeba minimum 3 graczy.'),
            'playing': ('🎭 Gra trwa', f'Runda kończy się <t:{lobby.end_at}:R>. Zaczyna <@{lobby.starter_id}>.'),
            'finished': ('🎭 Gra zakończona', 'Minął ustalony czas rundy.'),
            'cancelled': ('🎭 Lobby anulowane', 'Twórca anulował lobby.'),
            'expired': ('🎭 Lobby wygasło',
                        'Minęło 15 minut od utworzenia.' if lobby.expiry_reason == 'max_duration'
                        else 'Minęło 10 minut bez aktywności.'),
        }[lobby.state]
        if lobby.associations:
            title = title.replace('🎭', '🧩').replace('Lobby Impostora', 'Lobby Skojarzeń')
        container = discord.ui.Container(accent_color=0x5865F2 if active else 0x747F8D)
        add_logo(container)
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f'# {title}\n{status}\n'
            f'**Twórca:** <@{lobby.owner_id}>\n'
            f'**Gracze:** `{len(lobby.players)}/{lobby.capacity}` ▫️ **{"Bez hasła" if lobby.associations else "Impostorzy"}:** `{shown_impostors}`\n'
            f'**Czas gry:** `{lobby.duration} min`\n' +
            ('' if lobby.associations else f'**Podpowiedzi:** `{"Tak" if lobby.hints else "Nie"}`\n') +
            f'**Słowniki:** `{dictionary_names(lobby.dictionaries, lobby.key[0])}`'
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('### Gracze\n' + '\n'.join(f'👤 <@{user_id}>' for user_id in lobby.players)))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(
            LobbyButton(lobby, 'join', 'Dołącz', discord.ButtonStyle.success, disabled=not active or full),
            LobbyButton(lobby, 'start', 'Start', discord.ButtonStyle.primary,
                        disabled=not active or len(lobby.players) < 3),
            LobbyButton(lobby, 'cancel', 'Anuluj', discord.ButtonStyle.danger, disabled=not active),
        ))
        if active:
            container.add_item(discord.ui.TextDisplay(
                '-# Start i Anuluj: tylko twórca • lobby wygasa po 10 minutach bezczynności '
                'lub 15 minutach od utworzenia'
            ))
        elif lobby.state == 'playing':
            container.add_item(discord.ui.ActionRow(
                LobbyButton(lobby, 'reveal', 'Pokaż moją kartę', discord.ButtonStyle.primary),
                LobbyButton(lobby, 'end_game', 'Zakończ grę', discord.ButtonStyle.danger),
                LobbyButton(lobby, 'show_impostor', 'Pokaż impostora', discord.ButtonStyle.secondary),
            ))
        elif lobby.state == 'finished' and lobby.roles and not lobby.impostors_revealed:
            container.add_item(discord.ui.ActionRow(
                LobbyButton(lobby, 'show_impostor', 'Pokaż impostora', discord.ButtonStyle.secondary),
            ))
        if lobby.associations and lobby.order:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(
                '### Kolejność pytań\n' + '\n'.join(
                    f'{index + 1}. <@{user_id}> → <@{lobby.order[(index + 1) % len(lobby.order)]}>'
                    for index, user_id in enumerate(lobby.order)
                ) + '\nKażdy pyta kolejną osobę. Po pełnym okrążeniu powtarzajcie tę samą kolejność. '
                'Odpowiadajcie skojarzeniami z hasłem, nie zdradzając go wprost.'
            ))
        if lobby.associations:
            association_labels(container)
        self.add_item(container)


class PanelButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action_name = action

    async def callback(self, interaction):
        view = self.view
        await view.action(interaction, self.action_name)


class PlayerCountSelect(discord.ui.Select):
    def __init__(self, count):
        super().__init__(placeholder='Liczba graczy', options=[
            discord.SelectOption(label=f'{value} graczy', value=str(value), default=value == count)
            for value in range(3, 11)
        ])

    async def callback(self, interaction):
        view = self.view
        view.capacity = int(self.values[0])
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class HintsSelect(discord.ui.Select):
    def __init__(self, hints):
        super().__init__(placeholder='Podpowiedzi dla impostora', options=[
            discord.SelectOption(label='Podpowiedzi: tak', value='yes', default=hints),
            discord.SelectOption(label='Podpowiedzi: nie', value='no', default=not hints),
        ])

    async def callback(self, interaction):
        view = self.view
        view.hints = self.values[0] == 'yes'
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class DurationSelect(discord.ui.Select):
    def __init__(self, duration):
        options = [3, 4, 5, 6, 8, 10, 15]
        super().__init__(placeholder='Czas gry', options=[
            discord.SelectOption(label=f'{value} minut', value=str(value), default=value == duration)
            for value in options
        ])

    async def callback(self, interaction):
        view = self.view
        view.duration = int(self.values[0])
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class DictionarySelect(discord.ui.Select):
    def __init__(self, records, selected, page, pages):
        self.page_records = records[page * CUSTOM_DICTIONARIES_PER_PAGE:
                                    (page + 1) * CUSTOM_DICTIONARIES_PER_PAGE]
        options = [discord.SelectOption(
            label=STANDARD_DICTIONARY_NAME,
            value=STANDARD_DICTIONARY_ID,
            description=f'{len(load_standard_dictionary())} wbudowanych haseł',
            default=STANDARD_DICTIONARY_ID in selected,
        )]
        options.append(discord.SelectOption(
            label=INTERNET_DICTIONARY_NAME,
            value=INTERNET_DICTIONARY_ID,
            description=f'{len(load_internet_dictionary())} haseł o polskim internecie i memach',
            default=INTERNET_DICTIONARY_ID in selected,
        ))
        options.extend(discord.SelectOption(
            label=record['name'], value=record['id'],
            description=f'{len(record["words"])} haseł', default=record['id'] in selected,
        ) for record in self.page_records)
        super().__init__(placeholder=f'Słowniki · strona {page + 1}/{pages}', min_values=0,
                         max_values=len(options), options=options)

    async def callback(self, interaction):
        view = self.view
        selected = set(view.dictionaries)
        visible = {option.value for option in self.options}
        selected.difference_update(visible)
        selected.update(self.values)
        available = [STANDARD_DICTIONARY_ID, INTERNET_DICTIONARY_ID,
                     *(record['id'] for record in list_dictionaries(view.key[0]))]
        view.dictionaries = tuple(key for key in available if key in selected)
        view.render()
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


class CreateDictionaryModal(discord.ui.Modal):
    name_input = discord.ui.Label(text='Nazwa słownika', description='Np. Minecraft, zwierzęta, przedmioty.',
                                  component=discord.ui.TextInput(min_length=2, max_length=60))
    words_input = discord.ui.Label(text='Słowa oddzielone przecinkami', description='2–200 haseł, każde do 64 znaków. Spacje i powtórzenia zostaną uporządkowane.',
                                   component=discord.ui.TextInput(style=discord.TextStyle.paragraph,
                                                                  placeholder='mucha, trawnik, dom, drzewo',
                                                                  min_length=3, max_length=4000))

    def __init__(self, panel, record=None):
        super().__init__(title='Edytuj słownik' if record else ('Utwórz słownik Skojarzeń' if panel.associations else 'Utwórz słownik Impostora'), timeout=600)
        self.panel = panel
        self.record_id = record['id'] if record else None
        self.name_input.component.default = record['name'] if record else panel.dictionary_name_draft
        self.words_input.component.default = ','.join(record['words']) if record else panel.dictionary_words_draft

    async def interaction_check(self, interaction):
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction):
        self.panel.dictionary_name_draft = self.name_input.component.value
        self.panel.dictionary_words_draft = self.words_input.component.value
        try:
            if self.record_id:
                record = update_dictionary(self.panel.key[0], interaction.user.id, self.record_id,
                                           self.panel.dictionary_name_draft, self.panel.dictionary_words_draft)
            else:
                record = create_dictionary(self.panel.key[0], interaction.user.id,
                                           self.panel.dictionary_name_draft, self.panel.dictionary_words_draft)
        except ValueError as error:
            await reply(interaction, str(error))
            return
        except OSError:
            logger.exception('Could not save impostor dictionary')
            await reply(interaction, 'Nie udało się zapisać zmian. Spróbuj ponownie.')
            return
        self.panel.dictionary_name_draft = ''
        self.panel.dictionary_words_draft = ''
        self.panel.screen = 'dictionaries'
        self.panel.dictionary_page = 0
        self.panel.render()
        if self.panel.is_finished():
            await interaction.response.send_message(
                f'Słownik zapisany: {len(record["words"])} haseł. Otwórz /{"skojarzenia" if self.panel.associations else "impostor"}, aby go zobaczyć.',
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.edit_message(view=self.panel, allowed_mentions=discord.AllowedMentions.none())

    async def on_error(self, interaction, error):
        await self.panel.on_error(interaction, error, None)


class ImpostorPanel(ImpostorView):
    def __init__(self, owner_id, guild_id, channel, associations=False):
        super().__init__(timeout=600)
        self.associations = associations
        self.lobbies = ASSOCIATION_LOBBIES if associations else LOBBIES
        self.owner_id = owner_id
        self.key = (guild_id, channel.id)
        self.channel = channel
        self.screen = 'home'
        self.capacity = 4
        self.hints = False
        self.duration = 10
        self.dictionaries = (STANDARD_DICTIONARY_ID, INTERNET_DICTIONARY_ID)
        self.dictionary_page = 0
        self.dictionary_select_page = 0
        self.selected_dictionary_id = None
        self.dictionary_name_draft = ''
        self.dictionary_words_draft = ''
        self.message = None
        self.render()

    def render(self):
        self.clear_items()
        container = discord.ui.Container(accent_color=0x5865F2)
        add_logo(container)
        container.add_item(discord.ui.Separator())
        if self.screen == 'create':
            available_ids = {record['id'] for record in list_dictionaries(self.key[0])}
            self.dictionaries = tuple(key for key in self.dictionaries
                                      if key in (STANDARD_DICTIONARY_ID, INTERNET_DICTIONARY_ID) or key in available_ids)
            container.add_item(discord.ui.TextDisplay(
                '# 🎭 Stwórz lobby\nWybierz liczbę graczy, czas gry, podpowiedzi i słowniki.\n'
                f'**Gracze:** `{self.capacity}` ▫️ **{"Bez hasła" if self.associations else "Impostorzy"}:** `{1 if self.associations else impostor_count(self.capacity)}`\n'
                f'**Czas gry:** `{self.duration} min`\n' +
                ('' if self.associations else f'**Podpowiedzi:** `{"Tak" if self.hints else "Nie"}`\n') +
                f'**Słowniki:** `{dictionary_names(self.dictionaries, self.key[0])}`\n'
                '-# Liczba graczy obejmuje Ciebie — twórca dołącza automatycznie.'
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(PlayerCountSelect(self.capacity)))
            if not self.associations:
                container.add_item(discord.ui.ActionRow(HintsSelect(self.hints)))
            container.add_item(discord.ui.ActionRow(DurationSelect(self.duration)))
            records = list_dictionaries(self.key[0])
            select_pages = max(1, (len(records) + CUSTOM_DICTIONARIES_PER_PAGE - 1) // CUSTOM_DICTIONARIES_PER_PAGE)
            self.dictionary_select_page = max(0, min(self.dictionary_select_page, select_pages - 1))
            container.add_item(discord.ui.ActionRow(
                DictionarySelect(records, self.dictionaries, self.dictionary_select_page, select_pages)
            ))
            controls = []
            if select_pages > 1:
                controls.extend([
                    PanelButton('dictionary_select_prev', 'Poprzednia',
                                disabled=self.dictionary_select_page == 0),
                    PanelButton('dictionary_select_next', 'Następna',
                                disabled=self.dictionary_select_page == select_pages - 1),
                ])
            controls.extend([
                PanelButton('publish', 'Utwórz lobby', discord.ButtonStyle.success),
                PanelButton('home', 'Wróć'),
            ])
            container.add_item(discord.ui.ActionRow(*controls))
        elif self.screen in ('dictionary_detail', 'dictionary_delete_confirm'):
            record = next((item for item in list_dictionaries(self.key[0])
                           if item['id'] == self.selected_dictionary_id), None)
            if record is None or record['owner_id'] != self.owner_id:
                container.add_item(discord.ui.TextDisplay('# 📚 Słownik niedostępny\nNie można go już znaleźć.'))
                container.add_item(discord.ui.ActionRow(PanelButton('dictionaries', 'Wróć do słowników')))
            elif self.screen == 'dictionary_detail':
                name = discord.utils.escape_markdown(record['name'])
                preview = ', '.join(record['words'][:12])
                if len(preview) > 500:
                    preview = preview[:497] + '…'
                preview = discord.utils.escape_markdown(preview)
                container.add_item(discord.ui.TextDisplay(
                    f'# 📚 {name}\n{len(record["words"])} haseł · '
                    f'bez podpowiedzi: {count_words_without_hints(record["words"])}\n\n{preview}'
                ))
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.ActionRow(
                    PanelButton(f'dictionary_edit:{record["id"]}', 'Edytuj', discord.ButtonStyle.primary),
                    PanelButton(f'dictionary_delete:{record["id"]}', 'Usuń', discord.ButtonStyle.danger),
                    PanelButton('dictionaries', 'Wróć'),
                ))
            else:
                name = discord.utils.escape_markdown(record['name'])
                container.add_item(discord.ui.TextDisplay(
                    f'# Usuń słownik?\nCzy na pewno usunąć **{name}**? Tej operacji nie można cofnąć.'
                ))
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.ActionRow(
                    PanelButton(f'dictionary_delete_confirm:{record["id"]}', 'Usuń słownik', discord.ButtonStyle.danger),
                    PanelButton('dictionary_delete_cancel', 'Anuluj'),
                ))
        elif self.screen == 'dictionaries':
            records = list_dictionaries(self.key[0])
            pages = max(1, (len(records) + 3) // 4)
            self.dictionary_page = max(0, min(self.dictionary_page, pages - 1))
            container.add_item(discord.ui.TextDisplay('# 📚 Słowniki\nWłasne zestawy słów dostępne na tym serwerze.'))
            container.add_item(discord.ui.Separator())
            if not records:
                container.add_item(discord.ui.TextDisplay('Nie ma jeszcze własnych słowników. Utwórz pierwszy poniżej.'))
            for record in records[self.dictionary_page * 4:(self.dictionary_page + 1) * 4]:
                name = discord.utils.escape_markdown(record['name'])
                preview = ', '.join(record['words'][:6])
                if len(preview) > 160:
                    preview = preview[:157] + '…'
                elif len(record['words']) > 6:
                    preview += ', …'
                preview = discord.utils.escape_markdown(preview)
                card = discord.ui.TextDisplay(
                    f'### {name}\n{preview}\n-# {len(record["words"])} haseł · '
                    f'bez podpowiedzi: {count_words_without_hints(record["words"])} · '
                    f'autor <@{record["owner_id"]}>'
                )
                if record['owner_id'] == self.owner_id:
                    container.add_item(discord.ui.Section(
                        card,
                        accessory=PanelButton(f'dictionary_manage:{record["id"]}', 'Zarządzaj'),
                    ))
                else:
                    container.add_item(card)
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(
                PanelButton('dictionary_create', 'Utwórz słownik', discord.ButtonStyle.success),
                PanelButton('home', 'Wróć'),
            ))
            if pages > 1:
                container.add_item(discord.ui.TextDisplay(f'-# Strona {self.dictionary_page + 1} z {pages}'))
                container.add_item(discord.ui.ActionRow(
                    PanelButton('dictionary_prev', 'Poprzednia', disabled=self.dictionary_page == 0),
                    PanelButton('dictionary_next', 'Następna', disabled=self.dictionary_page == pages - 1),
                ))
        else:
            container.add_item(discord.ui.TextDisplay('# 🎭 Impostor\nStwórz lobby albo dołącz do gry na tym kanale.'))
            lobby = self.lobbies.get(self.key)
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay('### Nowa gra\nUstaw liczbę graczy, podpowiedzi i słowniki.'),
                accessory=PanelButton('create', 'Stwórz lobby', discord.ButtonStyle.primary, disabled=lobby is not None),
            ))
            join_text = 'Na tym kanale nie ma jeszcze lobby.'
            if lobby:
                join_text = (
                    f'**Gracze:** `{len(lobby.players)}/{lobby.capacity}`\n'
                    f'**Twórca:** <@{lobby.owner_id}>'
                )
                if lobby.message:
                    join_text += f'\n[Przejdź do lobby]({lobby.message.jump_url})'
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay(f'### Dołącz do gry\n{join_text}'),
                accessory=PanelButton('join', 'Dołączono' if lobby and self.owner_id in lobby.players else 'Dołącz',
                                      discord.ButtonStyle.success,
                                      disabled=lobby is None or len(lobby.players) >= lobby.capacity or self.owner_id in lobby.players),
            ))
            container.add_item(discord.ui.ActionRow(
                PanelButton('home', 'Odśwież'),
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay('### Słowniki\nTwórz własne zestawy słów, np. z wybranej gry.'),
                accessory=PanelButton('dictionaries', 'Słowniki'),
            ))
        self.footer = discord.ui.TextDisplay('-# Panel prywatny • wygasa po 10 minutach bezczynności')
        container.add_item(self.footer)
        if self.associations:
            association_labels(container)
        self.add_item(container)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await reply(interaction, 'Otwórz własny panel przez /skojarzenia.' if self.associations else 'Otwórz własny panel przez /impostor.')
        return False

    async def action(self, interaction, action):
        if action in ('home', 'create', 'dictionaries', 'dictionary_prev', 'dictionary_next',
                      'dictionary_select_prev', 'dictionary_select_next'):
            if action == 'dictionary_prev':
                self.dictionary_page -= 1
            elif action == 'dictionary_next':
                self.dictionary_page += 1
            elif action == 'dictionary_select_prev':
                self.dictionary_select_page -= 1
            elif action == 'dictionary_select_next':
                self.dictionary_select_page += 1
            elif action == 'dictionaries':
                self.dictionary_page = 0
            elif action == 'create':
                self.dictionary_select_page = 0
            if action not in ('dictionary_prev', 'dictionary_next',
                              'dictionary_select_prev', 'dictionary_select_next'):
                self.screen = action
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
        elif action == 'dictionary_create':
            await interaction.response.send_modal(CreateDictionaryModal(self))
        elif action.startswith('dictionary_manage:'):
            self.selected_dictionary_id = action.partition(':')[2]
            self.screen = 'dictionary_detail'
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
        elif action.startswith('dictionary_edit:'):
            dictionary_id = action.partition(':')[2]
            record = next((item for item in list_dictionaries(self.key[0]) if item['id'] == dictionary_id), None)
            if record is None or record['owner_id'] != self.owner_id:
                await reply(interaction, 'Nie znaleziono słownika, który możesz edytować.')
                return
            await interaction.response.send_modal(CreateDictionaryModal(self, record))
        elif action.startswith('dictionary_delete:'):
            self.selected_dictionary_id = action.partition(':')[2]
            self.screen = 'dictionary_delete_confirm'
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
        elif action == 'dictionary_delete_cancel':
            self.screen = 'dictionary_detail'
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
        elif action.startswith('dictionary_delete_confirm:'):
            dictionary_id = action.partition(':')[2]
            try:
                record = delete_dictionary(self.key[0], self.owner_id, dictionary_id)
            except ValueError as error:
                await reply(interaction, str(error))
                self.screen = 'dictionaries'
                self.render()
                await interaction.edit_original_response(view=self, allowed_mentions=discord.AllowedMentions.none())
                return
            except OSError:
                logger.exception('Could not delete impostor dictionary')
                await reply(interaction, 'Nie udało się usunąć słownika. Spróbuj ponownie.')
                return
            self.screen = 'dictionaries'
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
            await interaction.followup.send(
                f'Usunięto słownik **{discord.utils.escape_markdown(record["name"])}**.',
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        elif action == 'join':
            lobby = self.lobbies.get(self.key)
            if lobby:
                await lobby.action(interaction, 'join')
                self.render()
                await interaction.edit_original_response(view=self, allowed_mentions=discord.AllowedMentions.none())
            else:
                await reply(interaction, 'Na tym kanale nie ma lobby. Najpierw ktoś musi je stworzyć.')
        elif action == 'publish':
            if not self.dictionaries:
                await reply(interaction, 'Wybierz przynajmniej jeden słownik.')
                return
            await interaction.response.defer(ephemeral=True)
            async with _CREATE_LOCK:
                if self.key in self.lobbies:
                    await reply(interaction, 'Na tym kanale istnieje już lobby. Dołącz do niego albo poczekaj na zakończenie.')
                    return
                lobby = Lobby(*self.key, self.owner_id, self.capacity, self.hints, self.duration,
                              self.dictionaries, interaction, associations=self.associations)
                self.lobbies[self.key] = lobby
                try:
                    lobby.message = await self.channel.send(view=lobby.view, file=logo_file(self.associations),
                                                            allowed_mentions=discord.AllowedMentions.none())
                except BaseException:
                    lobby.release()
                    raise
                lobby.touch()
                lobby.timer = asyncio.create_task(lobby.watch_timeout())
            self.screen = 'home'
            self.render()
            await interaction.edit_original_response(view=self, allowed_mentions=discord.AllowedMentions.none())
            await reply(interaction, f'Lobby utworzone! Dołączono Cię automatycznie. {lobby.message.jump_url}')

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        self.footer.content = f'-# Panel wygasł. Otwórz nowy przez /{"skojarzenia" if self.associations else "impostor"}.'
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass


async def setup_impostor_commands(client, tree, guild_id=None):
    guild = discord.Object(id=guild_id) if guild_id else None

    @tree.command(name='impostor', description='Otwiera panel lobby i własnych słowników Impostora', guild=guild)
    @app_commands.guild_only()
    async def impostor(interaction: discord.Interaction):
        panel = ImpostorPanel(interaction.user.id, interaction.guild_id, interaction.channel)
        await interaction.response.send_message(view=panel, file=logo_file(), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
        panel.message = await interaction.original_response()


def association_labels(container):
    """Use the shared lobby controls with association-specific wording."""
    for item in container.walk_children():
        if isinstance(item, discord.ui.TextDisplay):
            item.content = (item.content.replace('🎭 Impostor', '🧩 Skojarzenia')
                            .replace('liczbę graczy, czas gry, podpowiedzi i słowniki', 'liczbę graczy, czas gry i słowniki')
                            .replace('liczbę graczy, podpowiedzi i słowniki', 'liczbę graczy, czas gry i słowniki'))
        elif isinstance(item, discord.ui.Button) and item.label == 'Pokaż impostora':
            item.label = 'Pokaż osobę bez hasła'


async def setup_associations_commands(client, tree, guild_id=None):
    guild = discord.Object(id=guild_id) if guild_id else None

    @tree.command(name='skojarzenia', description='Gra w pytania i skojarzenia: jedna osoba nie zna hasła', guild=guild)
    @app_commands.guild_only()
    async def skojarzenia(interaction: discord.Interaction):
        panel = ImpostorPanel(interaction.user.id, interaction.guild_id, interaction.channel, associations=True)
        await interaction.response.send_message(view=panel, file=logo_file(True), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
        panel.message = await interaction.original_response()
