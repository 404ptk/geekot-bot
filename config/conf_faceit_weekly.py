"""Scheduled FACEIT summary configuration screens."""
import discord
from config.conf_panel import ConfigView, PanelContent
from config import conf_faceit_weekly_settings as weekly_config


class FaceitWeeklyView(ConfigView):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        interval = weekly_config.load_config()['interval_weeks']
        for option in self.frequency.options:
            option.default = int(option.value) == interval

    def content(self, notice=None):
        config = weekly_config.load_config()
        content = PanelContent(title='⚙️ Faceit → Tygodniówka',
                              description='Automatyczna publikacja podsumowania statystyk Faceit.')
        content.add_field(name='Częstotliwość', value='Raz na tydzień' if config['interval_weeks'] == 1 else 'Co dwa tygodnie')
        content.add_field(name='Kanał wysyłki', value=f"<#{config['channel_id']}>", action='channel')
        if notice:
            content.add_field(name='Wynik', value=notice)
        content.set_footer(text='Wysyłka w poniedziałek. Zmiany działają bez restartu; odstęp liczony od ostatniej publikacji.')
        return content

    @discord.ui.select(placeholder='Wybierz częstotliwość…', row=0, options=[
        discord.SelectOption(label='Raz na tydzień', value='1'),
        discord.SelectOption(label='Co dwa tygodnie', value='2'),
    ])
    async def frequency(self, interaction, select):
        weekly_config.update_config(interval_weeks=int(select.values[0]))
        view = FaceitWeeklyView(self.owner_id)
        await self.show(interaction, view, view.content('Zapisano częstotliwość wysyłki.'))

    @discord.ui.button(label='Wybierz kanał', style=discord.ButtonStyle.primary, row=1)
    async def channel(self, interaction, button):
        view = WeeklyChannelView(self.owner_id)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Wróć do Faceit', row=2)
    async def back(self, interaction, button):
        from config.conf_faceit import FaceitView
        view = FaceitView(self.owner_id)
        await self.show(interaction, view, view.content())


class WeeklyChannelView(ConfigView):
    def content(self):
        return PanelContent(title='⚙️ Tygodniówka → Kanał wysyłki',
                             description='Wybierz kanał automatycznych podsumowań Faceit.')

    @discord.ui.select(cls=discord.ui.ChannelSelect,
                       channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                       placeholder='Wybierz kanał wysyłki…')
    async def channel(self, interaction, select):
        channel = interaction.guild.get_channel(select.values[0].id) if interaction.guild else None
        if channel is None:
            await interaction.response.send_message('Nie można odczytać tego kanału. Spróbuj ponownie.', ephemeral=True)
            return
        permissions = channel.permissions_for(interaction.guild.me)
        if not all((permissions.view_channel, permissions.send_messages, permissions.embed_links, permissions.attach_files)):
            await interaction.response.send_message(
                'Bot potrzebuje dostępu do kanału, wysyłania wiadomości, osadzania linków i załączania plików.', ephemeral=True)
            return
        weekly_config.update_config(channel_id=channel.id)
        view = FaceitWeeklyView(self.owner_id)
        await self.show(interaction, view, view.content(f'Zapisano kanał <#{channel.id}>.'))

    @discord.ui.button(label='Wróć do tygodniówki', row=1)
    async def back(self, interaction, button):
        view = FaceitWeeklyView(self.owner_id)
        await self.show(interaction, view, view.content())
