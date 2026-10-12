"""Impostor lobby setup and joining through Components V2."""
import asyncio
import logging
import time
from pathlib import Path

import discord
from discord import app_commands
from games.impostor.dictionaries import create_dictionary, list_dictionaries, update_dictionary

logger = logging.getLogger(__name__)
LOBBY_TIMEOUT = 600
LOGO_FILE = Path(__file__).resolve().with_name('logo.webp')
LOGO_FILENAME = 'impostor_logo.webp'
DICTIONARIES = {'custom': 'Customowe', 'standard': 'Zwyczajne'}
# One open lobby per channel; separate channels can host separate groups.
LOBBIES = {}
_CREATE_LOCK = asyncio.Lock()


def impostor_count(players):
    return 1 if players <= 5 else 2 if players <= 8 else 3


def dictionary_names(selected):
    return ', '.join(label for key, label in DICTIONARIES.items() if key in selected)


def logo_file():
    return discord.File(LOGO_FILE, filename=LOGO_FILENAME)


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


class Lobby:
    def __init__(self, guild_id, channel_id, owner_id, capacity, hints, dictionaries=('standard',)):
        if not 3 <= capacity <= 10:
            raise ValueError('Lobby musi mieć od 3 do 10 graczy.')
        if not dictionaries or not set(dictionaries) <= DICTIONARIES.keys():
            raise ValueError('Wybierz przynajmniej jeden dostępny słownik.')
        self.key = (guild_id, channel_id)
        self.owner_id = owner_id
        self.capacity = capacity
        self.impostors = impostor_count(capacity)
        self.hints = hints
        self.dictionaries = tuple(key for key in DICTIONARIES if key in dictionaries)
        self.players = [owner_id]
        self.state = 'waiting'
        self.message = None
        self.deadline = time.monotonic() + LOBBY_TIMEOUT
        self.lock = asyncio.Lock()
        self.activity = asyncio.Event()
        self.timer = None
        self.view = LobbyView(self)

    def touch(self):
        self.deadline = time.monotonic() + LOBBY_TIMEOUT
        self.activity.set()

    def release(self):
        if LOBBIES.get(self.key) is self:
            del LOBBIES[self.key]
        self.view.stop()
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()

    async def finish(self, state):
        self.state = state
        self.view.render()
        try:
            if self.message:
                await self.message.edit(view=self.view, allowed_mentions=discord.AllowedMentions.none())
        finally:
            self.release()

    async def watch_timeout(self):
        try:
            while self.state == 'waiting':
                self.activity.clear()
                remaining = max(0, self.deadline - time.monotonic())
                try:
                    await asyncio.wait_for(self.activity.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    async with self.lock:
                        if self.state == 'waiting' and time.monotonic() >= self.deadline:
                            await self.finish('expired')
                            return
        except discord.HTTPException:
            logger.warning('Could not update expired impostor lobby', exc_info=True)

    async def action(self, interaction, action):
        if action in ('start', 'cancel') and interaction.user.id != self.owner_id:
            await reply(interaction, 'Tylko twórca lobby może je rozpocząć lub anulować.')
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            if self.state != 'waiting' or LOBBIES.get(self.key) is not self:
                await reply(interaction, 'To lobby jest już zamknięte.')
                return
            if time.monotonic() >= self.deadline:
                await self.finish('expired')
                await reply(interaction, 'Lobby wygasło po 10 minutach bezczynności.')
                return
            if action == 'join':
                if interaction.user.id in self.players:
                    await reply(interaction, 'Jesteś już w tym lobby.')
                    return
                if len(self.players) >= self.capacity:
                    await reply(interaction, 'Lobby jest pełne.')
                    return
                self.players.append(interaction.user.id)
                self.view.render()
                try:
                    await self.message.edit(view=self.view, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException as error:
                    self.players.remove(interaction.user.id)
                    if isinstance(error, discord.NotFound):
                        self.state = 'cancelled'
                        self.release()
                    self.view.render()
                    raise
                self.touch()
                await reply(interaction, f'Dołączono do lobby! {self.message.jump_url}')
            elif action == 'start':
                if len(self.players) != self.capacity:
                    await reply(interaction, f'Brakuje jeszcze {self.capacity - len(self.players)} graczy.')
                    return
                # Game roles and words will be implemented in the next stage.
                await self.finish('started')
                await reply(interaction, 'Skład gotowy do gry — dołączanie zostało zamknięte.')
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
        title, status = {
            'waiting': ('🎭 Lobby Impostora', 'Komplet graczy — twórca może kliknąć Start.' if full else 'Dołącz przyciskiem poniżej.'),
            'started': ('🎭 Skład gotowy do gry', 'Dołączanie zamknięte.'),
            'cancelled': ('🎭 Lobby anulowane', 'Twórca anulował lobby.'),
            'expired': ('🎭 Lobby wygasło', 'Anulowano po 10 minutach bezczynności.'),
        }[lobby.state]
        container = discord.ui.Container(accent_color=0x5865F2 if active else 0x747F8D)
        add_logo(container)
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f'# {title}\n{status}\n'
            f'**Twórca:** <@{lobby.owner_id}>\n'
            f'**Gracze:** `{len(lobby.players)}/{lobby.capacity}` ▫️ **Impostorzy:** `{lobby.impostors}`\n'
            f'**Podpowiedzi:** `{"Tak" if lobby.hints else "Nie"}`\n'
            f'**Słowniki:** `{dictionary_names(lobby.dictionaries)}`'
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('### Gracze\n' + '\n'.join(f'👤 <@{user_id}>' for user_id in lobby.players)))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(
            LobbyButton(lobby, 'join', 'Dołącz', discord.ButtonStyle.success, disabled=not active or full),
            LobbyButton(lobby, 'start', 'Start', discord.ButtonStyle.primary, disabled=not active or not full),
            LobbyButton(lobby, 'cancel', 'Anuluj', discord.ButtonStyle.danger, disabled=not active),
        ))
        if active:
            container.add_item(discord.ui.TextDisplay('-# Start i Anuluj: tylko twórca • lobby wygasa po 10 minutach bezczynności'))
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


class DictionarySelect(discord.ui.Select):
    def __init__(self, selected):
        super().__init__(placeholder='Wybierz słowniki — można zaznaczyć oba', min_values=1,
                         max_values=len(DICTIONARIES), options=[
            discord.SelectOption(label=label, value=key, default=key in selected)
            for key, label in DICTIONARIES.items()
        ])

    async def callback(self, interaction):
        view = self.view
        view.dictionaries = tuple(key for key in DICTIONARIES if key in self.values)
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
        super().__init__(title='Edytuj słownik' if record else 'Utwórz słownik Impostora', timeout=600)
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
                f'Słownik zapisany: {len(record["words"])} haseł. Otwórz /impostor, aby go zobaczyć.',
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.edit_message(view=self.panel, allowed_mentions=discord.AllowedMentions.none())

    async def on_error(self, interaction, error):
        await self.panel.on_error(interaction, error, None)


class ImpostorPanel(ImpostorView):
    def __init__(self, owner_id, guild_id, channel):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.key = (guild_id, channel.id)
        self.channel = channel
        self.screen = 'home'
        self.capacity = 4
        self.hints = False
        self.dictionaries = ('standard',)
        self.dictionary_page = 0
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
            container.add_item(discord.ui.TextDisplay(
                '# 🎭 Stwórz lobby\nWybierz liczbę graczy, podpowiedzi i słowniki.\n'
                f'**Gracze:** `{self.capacity}` ▫️ **Impostorzy:** `{impostor_count(self.capacity)}`\n'
                f'**Podpowiedzi:** `{"Tak" if self.hints else "Nie"}`\n'
                f'**Słowniki:** `{dictionary_names(self.dictionaries)}`\n'
                '-# Liczba graczy obejmuje Ciebie — twórca dołącza automatycznie.'
            ))
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(PlayerCountSelect(self.capacity)))
            container.add_item(discord.ui.ActionRow(HintsSelect(self.hints)))
            container.add_item(discord.ui.ActionRow(DictionarySelect(self.dictionaries)))
            container.add_item(discord.ui.ActionRow(
                PanelButton('publish', 'Utwórz lobby', discord.ButtonStyle.success),
                PanelButton('home', 'Wróć'),
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
                    f'### {name}\n{preview}\n-# {len(record["words"])} haseł · autor <@{record["owner_id"]}>'
                )
                if record['owner_id'] == self.owner_id:
                    container.add_item(discord.ui.Section(
                        card,
                        accessory=PanelButton(f'dictionary_edit:{record["id"]}', 'Edytuj'),
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
            lobby = LOBBIES.get(self.key)
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
        self.add_item(container)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await reply(interaction, 'Otwórz własny panel przez /impostor.')
        return False

    async def action(self, interaction, action):
        if action in ('home', 'create', 'dictionaries', 'dictionary_prev', 'dictionary_next'):
            if action == 'dictionary_prev':
                self.dictionary_page -= 1
            elif action == 'dictionary_next':
                self.dictionary_page += 1
            elif action == 'dictionaries':
                self.dictionary_page = 0
            if action not in ('dictionary_prev', 'dictionary_next'):
                self.screen = action
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
        elif action == 'dictionary_create':
            await interaction.response.send_modal(CreateDictionaryModal(self))
        elif action.startswith('dictionary_edit:'):
            dictionary_id = action.partition(':')[2]
            record = next((item for item in list_dictionaries(self.key[0]) if item['id'] == dictionary_id), None)
            if record is None or record['owner_id'] != self.owner_id:
                await reply(interaction, 'Nie znaleziono słownika, który możesz edytować.')
                return
            await interaction.response.send_modal(CreateDictionaryModal(self, record))
        elif action == 'join':
            lobby = LOBBIES.get(self.key)
            if lobby:
                await lobby.action(interaction, 'join')
                self.render()
                await interaction.edit_original_response(view=self, allowed_mentions=discord.AllowedMentions.none())
            else:
                await reply(interaction, 'Na tym kanale nie ma lobby. Najpierw ktoś musi je stworzyć.')
        elif action == 'publish':
            await interaction.response.defer(ephemeral=True)
            async with _CREATE_LOCK:
                if self.key in LOBBIES:
                    await reply(interaction, 'Na tym kanale istnieje już lobby. Dołącz do niego albo poczekaj na zakończenie.')
                    return
                lobby = Lobby(*self.key, self.owner_id, self.capacity, self.hints, self.dictionaries)
                LOBBIES[self.key] = lobby
                try:
                    lobby.message = await self.channel.send(view=lobby.view, file=logo_file(),
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
        self.footer.content = '-# Panel wygasł. Otwórz nowy przez /impostor.'
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
