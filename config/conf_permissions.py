"""Administrator-only editor for global and per-command role thresholds."""
import discord
from config.conf_panel import ConfigView
from config import conf_permissions_settings as permissions


def role_label(guild, role_id):
    if role_id is None:
        return 'Tylko administrator'
    role = guild.get_role(role_id)
    return f'{role.mention} i role wyżej' if role else f'Usunięta rola (ID: {role_id}) — tylko administrator'


class CommandSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder='Wybierz komendę…', row=1, options=[
            discord.SelectOption(label=f'/{name}', value=name, description=description)
            for name, description in permissions.MODERATOR_COMMANDS.items()
        ])

    async def callback(self, interaction):
        view = PermissionRoleView(self.view.owner_id, interaction.guild, self.values[0])
        await self.view.show(interaction, view, view.embed())


class PermissionsView(ConfigView):
    def __init__(self, owner_id, guild):
        super().__init__(owner_id)
        self.guild = guild
        self.add_item(CommandSelect())

    def embed(self, notice=None):
        config = permissions.load_config(self.guild.id)
        embed = discord.Embed(title='⚙️ Permisje', color=discord.Color.blurple(), description=(
            'Wybrana ranga i wszystkie role wyżej w hierarchii otrzymują dostęp. Administrator zawsze ma dostęp.\n'
            'Ustawienie komendy zastępuje rangę globalną. Domyślnie komendy korzystają z ustawienia globalnego.'))
        embed.add_field(name='Globalna ranga moderatora', value=role_label(self.guild, config['moderator_role_id']), inline=False)
        for command in permissions.MODERATOR_COMMANDS:
            inherited = command not in config['commands']
            role_id = config['commands'].get(command, config['moderator_role_id'])
            embed.add_field(name=f'/{command}', value=('Globalnie: ' if inherited else 'Osobno: ') + role_label(self.guild, role_id), inline=False)
        if notice:
            embed.add_field(name='Wynik', value=notice, inline=False)
        embed.set_footer(text='Zmiany działają bez restartu.')
        return embed

    @discord.ui.button(label='Globalna ranga moderatora', style=discord.ButtonStyle.primary, row=0)
    async def global_role(self, interaction, button):
        view = PermissionRoleView(self.owner_id, interaction.guild)
        await self.show(interaction, view, view.embed())

    @discord.ui.button(label='Wróć do konfiguracji', row=2)
    async def back(self, interaction, button):
        from config.conf_menu import ConfigHomeView
        view = ConfigHomeView(self.owner_id)
        await self.show(interaction, view, view.embed())


class PermissionRoleView(ConfigView):
    def __init__(self, owner_id, guild, command=None):
        super().__init__(owner_id)
        self.guild = guild
        self.command = command
        self.inherit.disabled = command is None

    def embed(self):
        target = f'/{self.command}' if self.command else 'Globalna ranga moderatora'
        return discord.Embed(title=f'⚙️ Permisje → {target}', color=discord.Color.blurple(),
                             description='Wybierz minimalną rangę. Dostęp uzyskają także role wyżej w hierarchii serwera.')

    async def finish(self, interaction, notice):
        view = PermissionsView(self.owner_id, interaction.guild)
        await self.show(interaction, view, view.embed(notice))

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder='Wybierz minimalną rangę…', row=0)
    async def role(self, interaction, select):
        role = interaction.guild.get_role(select.values[0].id)
        if role is None or role.is_default():
            await interaction.response.send_message('Wybierz istniejącą rangę inną niż @everyone.', ephemeral=True)
            return
        permissions.set_role(interaction.guild.id, role.id, self.command)
        await self.finish(interaction, f'Zapisano rangę {role.mention} i role wyżej.')

    @discord.ui.button(label='Tylko administrator', row=1)
    async def admin_only(self, interaction, button):
        permissions.set_role(interaction.guild.id, None, self.command)
        await self.finish(interaction, 'Zapisano dostęp tylko dla administratora.')

    @discord.ui.button(label='Użyj rangi globalnej', row=1)
    async def inherit(self, interaction, button):
        permissions.set_role(interaction.guild.id, None, self.command, inherit=True)
        await self.finish(interaction, 'Komenda korzysta z ustawienia globalnego.')

    @discord.ui.button(label='Wróć do Permisji', row=2)
    async def back(self, interaction, button):
        await self.finish(interaction, 'Ustawienia pozostawiono bez zmian.')
