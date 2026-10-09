"""FACEIT LIVE screens for the shared /config panel."""
import discord

from config.conf_panel import ConfigView, PanelContent
from config import conf_faceit_live_settings as live_config
from config.conf_faceit_weekly import FaceitWeeklyView


class FaceitSectionSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='Wybierz funkcję Faceit…', options=[
            discord.SelectOption(label=title, value=key, description=description)
            for key, title, description, factory in FACEIT_SECTIONS
        ])

    async def callback(self, interaction):
        factory = next(factory for key, title, description, factory in FACEIT_SECTIONS
                       if key == self.values[0])
        view = factory(self.view.owner_id)
        await self.view.show(interaction, view, view.content())


class FaceitView(ConfigView):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.add_item(FaceitSectionSelect())

    def content(self):
        content = PanelContent(title='⚙️ Faceit', description='Wybierz funkcję, którą chcesz skonfigurować.')
        for key, title, description, factory in FACEIT_SECTIONS:
            content.add_field(name=title, value=description)
        return content

    @discord.ui.button(label='Menu główne', row=1)
    async def home(self, interaction, button):
        from config.conf_menu import ConfigHomeView
        view = ConfigHomeView(self.owner_id)
        await self.show(interaction, view, view.content())


class FaceitLiveView(ConfigView):
    def content(self, notice=None):
        config = live_config.load_config()
        names = ', '.join(discord.utils.escape_markdown(nick) for nick in config['players']) or 'Lista jest pusta.'
        content = PanelContent(title='⚙️ Faceit → LIVE',
                              description='Gracze widoczni na grafice Faceit LIVE i kanał jej publikacji.')
        content.add_field(name=f"Śledzeni gracze ({len(config['players'])})", value=names[:1020])
        content.add_field(name='Kanał wiadomości', value=f"<#{config['channel_id']}>", action='channel')
        if notice:
            content.add_field(name='Wynik', value=notice)
        content.set_footer(text='Zmiany zostaną uwzględnione przy kolejnym odświeżeniu, najpóźniej za około 5 minut.')
        return content

    @discord.ui.button(label='Dodaj gracza', style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await interaction.response.send_modal(AddPlayerModal(self))

    @discord.ui.button(label='Usuń gracza', style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        view = RemovePlayerView(self.owner_id)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Wybierz kanał', style=discord.ButtonStyle.primary)
    async def channel(self, interaction, button):
        view = ChannelView(self.owner_id)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Wróć do Faceit', row=1)
    async def home(self, interaction, button):
        view = FaceitView(self.owner_id)
        await self.show(interaction, view, view.content())


class AddPlayerModal(discord.ui.Modal, title='Dodaj gracza do Faceit LIVE'):
    nickname = discord.ui.Label(text='Nick FACEIT', description='Podaj dokładny nick z profilu FACEIT, np. utopiasz.',
                                component=discord.ui.TextInput(min_length=1, max_length=100))

    def __init__(self, panel):
        super().__init__(timeout=600)
        self.panel = panel

    async def interaction_check(self, interaction):
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction):
        try:
            nickname = live_config.add_player(self.nickname.component.value)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        view = FaceitLiveView(self.panel.owner_id)
        await self.panel.show(interaction, view, view.content(f'Dodano **{discord.utils.escape_markdown(nickname)}**.'))

    async def on_error(self, interaction, error):
        await self.panel.on_error(interaction, error, None)


class PlayerSelect(discord.ui.Select):
    def __init__(self, players):
        super().__init__(placeholder='Wybierz gracza do usunięcia…', options=[
            discord.SelectOption(label=nick, value=nick) for nick in players
        ])

    async def callback(self, interaction):
        view = RemovePlayerView(self.view.owner_id, self.view.page, self.values[0])
        await self.view.show(interaction, view, view.content())


class RemovePlayerView(ConfigView):
    def __init__(self, owner_id, page=0, selected=None):
        super().__init__(owner_id)
        players = sorted(live_config.load_config()['players'], key=str.lower)
        self.pages = max(1, (len(players) + 24) // 25)
        self.page = min(max(page, 0), self.pages - 1)
        self.selected = selected if selected in players else None
        if players:
            self.add_item(PlayerSelect(players[self.page * 25:(self.page + 1) * 25]))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == self.pages - 1
        self.confirm.disabled = self.selected is None

    def content(self):
        text = 'Wybierz gracza, a następnie kliknij „Usuń”.'
        if not live_config.load_config()['players']:
            text = 'Lista śledzonych graczy jest pusta.'
        if self.selected:
            text += f'\n\nWybrano: **{discord.utils.escape_markdown(self.selected)}**'
        content = PanelContent(title='⚙️ Faceit LIVE → Usuń gracza', description=text)
        content.set_footer(text=f'Strona {self.page + 1}/{self.pages}')
        return content

    @discord.ui.button(label='Poprzednia strona', row=1)
    async def previous(self, interaction, button):
        view = RemovePlayerView(self.owner_id, self.page - 1)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Następna strona', row=1)
    async def next_page(self, interaction, button):
        view = RemovePlayerView(self.owner_id, self.page + 1)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Usuń', style=discord.ButtonStyle.danger, row=2)
    async def confirm(self, interaction, button):
        try:
            live_config.remove_player(self.selected)
            notice = f'Usunięto **{discord.utils.escape_markdown(self.selected)}** ze śledzenia.'
        except ValueError as error:
            notice = str(error)
        view = FaceitLiveView(self.owner_id)
        await self.show(interaction, view, view.content(notice))

    @discord.ui.button(label='Wróć do Faceit LIVE', row=2)
    async def back(self, interaction, button):
        view = FaceitLiveView(self.owner_id)
        await self.show(interaction, view, view.content())


# Register further Faceit configuration screens alongside LIVE.
FACEIT_SECTIONS = (
    ('live', 'LIVE', 'Śledzeni gracze i kanał wiadomości', FaceitLiveView),
    ('weekly', 'Tygodniówka', 'Częstotliwość i kanał wysyłki podsumowań', FaceitWeeklyView),
)


class ChannelView(ConfigView):
    def content(self):
        return PanelContent(title='⚙️ Faceit LIVE → Kanał wiadomości',
                             description='Wybierz kanał, na którym bot ma publikować grafikę Faceit LIVE.')

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                       placeholder='Wybierz kanał wiadomości…')
    async def channel(self, interaction, select):
        selected = select.values[0]
        channel = interaction.guild.get_channel(selected.id) if interaction.guild else None
        if channel is None:
            await interaction.response.send_message('Nie można odczytać tego kanału. Spróbuj ponownie.', ephemeral=True)
            return
        permissions = channel.permissions_for(interaction.guild.me)
        if not all((permissions.view_channel, permissions.send_messages, permissions.attach_files,
                    permissions.read_message_history)):
            await interaction.response.send_message(
                'Bot potrzebuje na tym kanale dostępu do kanału, wysyłania wiadomości, załączania plików i czytania historii.',
                ephemeral=True)
            return
        live_config.set_channel(channel.id)
        view = FaceitLiveView(self.owner_id)
        await self.show(interaction, view, view.content(f'Zapisano kanał <#{channel.id}>.'))

    @discord.ui.button(label='Wróć do Faceit LIVE', row=1)
    async def back(self, interaction, button):
        view = FaceitLiveView(self.owner_id)
        await self.show(interaction, view, view.content())
