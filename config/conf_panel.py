"""Shared lifecycle and owner checks for private configuration screens."""
import discord
from dataclasses import dataclass, field


@dataclass
class PanelField:
    name: str
    value: str
    action: str | None = None


@dataclass
class PanelContent:
    title: str
    description: str = ''
    fields: list[PanelField] = field(default_factory=list)
    footer: str = ''

    def add_field(self, *, name, value, action=None):
        self.fields.append(PanelField(name, value, action))

    def set_footer(self, *, text):
        self.footer = text


async def require_administrator(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not interaction.permissions.administrator:
        await interaction.response.send_message(
            "Konfiguracja bota jest dostępna tylko dla administratorów serwera.", ephemeral=True)
        return False
    return True


class ConfigView(discord.ui.LayoutView):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.message = None
        # Keep the existing callback declarations while placing the controls
        # inside V2 sections and action rows when rendering each screen.
        callbacks = {}
        for base in reversed(type(self).__mro__):
            for name, callback in base.__dict__.items():
                if hasattr(callback, '__discord_ui_model_type__'):
                    callbacks[name] = callback
        for name, callback in callbacks.items():
            item = callback.__discord_ui_model_type__(**callback.__discord_ui_model_kwargs__)
            async def invoke(interaction, callback=callback, item=item):
                await callback(self, interaction, item)
            item.callback = invoke
            setattr(self, name, item)
            self.add_item(item)
        self._controls = None
        self._container = None
        self._footer_display = None

    def render(self, content):
        if self._controls is None:
            self._controls = list(self.children)
        self.clear_items()
        container = discord.ui.Container(accent_color=0x5865F2)
        container.add_item(discord.ui.TextDisplay(f"## {content.title}\n{content.description}".rstrip()))
        container.add_item(discord.ui.Separator())
        controls = list(self._controls)
        for entry in content.fields:
            text = f"**{entry.name}**\n{entry.value}"
            if entry.name == 'Wynik':
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.TextDisplay(f"**Informacja**\n{entry.value}"))
                continue
            accessory = getattr(self, entry.action, None) if entry.action else None
            if isinstance(accessory, discord.ui.Button) and accessory in controls:
                controls.remove(accessory)
                container.add_item(discord.ui.Section(text, accessory=accessory))
            else:
                container.add_item(discord.ui.TextDisplay(text))
        if content.fields:
            container.add_item(discord.ui.Separator())
        rows = {}
        for item in controls:
            rows.setdefault(item.row or 0, []).append(item)
        for row in sorted(rows):
            buttons = []
            for item in rows[row]:
                if isinstance(item, discord.ui.Button):
                    buttons.append(item)
                else:
                    if buttons:
                        container.add_item(discord.ui.ActionRow(*buttons))
                        buttons = []
                    container.add_item(discord.ui.ActionRow(item))
            if buttons:
                container.add_item(discord.ui.ActionRow(*buttons))
        container.add_item(discord.ui.Separator())
        footer = content.footer
        if 'wygasa' not in footer:
            footer = f'{footer}\nPanel prywatny • tylko administrator • wygasa po 10 minutach'.strip()
        self._footer_display = discord.ui.TextDisplay(f'-# {footer}')
        container.add_item(self._footer_display)
        self.add_item(container)
        self._container = container
        return self

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await require_administrator(interaction):
            return False
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Otwórz własny panel przez /config.", ephemeral=True)
            return False
        return True

    async def show(self, interaction: discord.Interaction, view, content):
        view.render(content)
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())
        view.message = interaction.message or self.message
        self.stop()

    async def on_timeout(self):
        for item in self.walk_children():
            if hasattr(item, 'disabled'):
                item.disabled = True
        if self._footer_display:
            self._footer_display.content = '-# Panel wygasł. Wpisz /config, aby otworzyć go ponownie.'
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        print(f"Config panel error: {error}")
        message = "Nie udało się wykonać operacji. Spróbuj ponownie."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
