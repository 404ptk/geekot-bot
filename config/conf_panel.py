"""Shared lifecycle and owner checks for private configuration screens."""
import discord


class ConfigView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Otwórz własny panel przez /config.", ephemeral=True)
            return False
        return True

    async def show(self, interaction: discord.Interaction, view, embed):
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = interaction.message or self.message
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Panel wygasł. Wpisz /config, aby otworzyć go ponownie.", view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        print(f"Config panel error: {error}")
        message = "Nie udało się wykonać operacji. Spróbuj ponownie."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


