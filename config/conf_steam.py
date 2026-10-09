"""Steam settings in the shared /config panel."""
import discord
from config.conf_panel import ConfigView
from config import conf_steam_settings as steam_config


class SteamView(ConfigView):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        config = steam_config.load_config()
        self.official.label = 'Wyłącz Steam' if config['official_enabled'] else 'Włącz Steam'
        self.github.label = 'Wyłącz GitHub' if config['github_enabled'] else 'Włącz GitHub'
        self.official.style = discord.ButtonStyle.danger if config['official_enabled'] else discord.ButtonStyle.success
        self.github.style = discord.ButtonStyle.danger if config['github_enabled'] else discord.ButtonStyle.success

    def embed(self, notice=None):
        config = steam_config.load_config()
        embed = discord.Embed(title='⚙️ Steam', description='Automatyczne aktualizacje Counter-Strike 2.',
                              color=discord.Color.blue())
        embed.add_field(name='Kanał aktualizacji CS2', value=f"<#{config['channel_id']}>", inline=False)
        for name, key in [('Oficjalne notki ze Steam', 'official_enabled'),
                          ('GitHub — GameTracking-CS2 (zmiany plików)', 'github_enabled')]:
            embed.add_field(name=name, value='Włączone' if config[key] else 'Wyłączone', inline=False)
        if notice:
            embed.add_field(name='Wynik', value=notice, inline=False)
        embed.set_footer(text='Zmiany działają bez restartu. Wyłączenie źródła wstrzymuje jego pobieranie i publikację.')
        return embed

    @discord.ui.button(label='Wybierz kanał', style=discord.ButtonStyle.primary)
    async def channel(self, interaction, button):
        view = SteamChannelView(self.owner_id)
        await self.show(interaction, view, view.embed())

    async def toggle(self, interaction, key):
        current = steam_config.load_config()
        steam_config.update_config(**{key: not current[key]})
        view = SteamView(self.owner_id)
        await self.show(interaction, view, view.embed('Zapisano ustawienia.'))

    @discord.ui.button(label='Steam', row=1)
    async def official(self, interaction, button):
        await self.toggle(interaction, 'official_enabled')

    @discord.ui.button(label='GitHub', row=1)
    async def github(self, interaction, button):
        await self.toggle(interaction, 'github_enabled')

    @discord.ui.button(label='Wróć do konfiguracji', row=2)
    async def back(self, interaction, button):
        from config.conf_menu import ConfigHomeView
        view = ConfigHomeView(self.owner_id)
        await self.show(interaction, view, view.embed())


class SteamChannelView(ConfigView):
    def embed(self):
        return discord.Embed(title='⚙️ Steam → Kanał aktualizacji CS2',
                             description='Wybierz wspólny kanał dla oficjalnych notek Steam i zmian plików z GitHuba.',
                             color=discord.Color.blue())

    @discord.ui.select(cls=discord.ui.ChannelSelect,
                       channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                       placeholder='Wybierz kanał aktualizacji…')
    async def channel(self, interaction, select):
        channel = interaction.guild.get_channel(select.values[0].id) if interaction.guild else None
        if channel is None:
            await interaction.response.send_message('Nie można odczytać tego kanału. Spróbuj ponownie.', ephemeral=True)
            return
        permissions = channel.permissions_for(interaction.guild.me)
        if not all((permissions.view_channel, permissions.send_messages,
                    permissions.embed_links, permissions.read_message_history)):
            await interaction.response.send_message(
                'Bot potrzebuje dostępu do kanału, wysyłania wiadomości, osadzania linków i czytania historii.',
                ephemeral=True)
            return
        steam_config.update_config(channel_id=channel.id)
        view = SteamView(self.owner_id)
        await self.show(interaction, view, view.embed(f'Zapisano kanał <#{channel.id}>.'))

    @discord.ui.button(label='Wróć do Steam', row=1)
    async def back(self, interaction, button):
        view = SteamView(self.owner_id)
        await self.show(interaction, view, view.embed())
