"""Cyfry: everyone gets the same random number 1-10, except one player who doesn't."""
import asyncio
import logging
import random

import discord
from discord import app_commands

logger = logging.getLogger(__name__)
MIN_PLAYERS = 3
MAX_PLAYERS = 15
LOBBY_TIMEOUT = 3600
# One game per channel.
LOBBIES = {}
NO_MENTIONS = discord.AllowedMentions.none()


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_MENTIONS)


def card_text(game, user_id):
    if user_id == game.impostor_id:
        return ('# ❓ Nie znasz cyfry\nWszyscy inni dostali tę samą cyfrę od 1 do 10. '
                'Nie daj się zdemaskować i spróbuj ją odgadnąć.')
    return f'# 🔢 Twoja cyfra: **{game.number}**\nJedna osoba jej nie zna — znajdźcie ją.'


class CyfryGame:
    def __init__(self, key, owner_id, owner_interaction):
        self.key = key
        self.owner_id = owner_id
        self.players = [owner_id]
        self.interactions = {owner_id: owner_interaction}
        self.state = 'waiting'
        self.round = 0
        self.number = None
        self.impostor_id = None
        self.starter_id = None
        self.revealed = False
        self.message = None
        self.lock = asyncio.Lock()
        self.view = CyfryView(self)

    def close(self):
        if LOBBIES.get(self.key) is self:
            del LOBBIES[self.key]
        self.view.stop()

    async def refresh(self):
        self.view.render()
        try:
            await self.message.edit(view=self.view, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            logger.warning('Could not update cyfry lobby', exc_info=True)

    async def send_card(self, interaction, user_id):
        await interaction.followup.send(card_text(self, user_id), ephemeral=True, allowed_mentions=NO_MENTIONS)

    async def new_round(self, interaction):
        previous = self.number
        self.number = random.choice([n for n in range(1, 11) if n != previous])
        self.impostor_id = random.choice(self.players)
        self.starter_id = random.choice(self.players)
        self.revealed = False
        self.round += 1
        self.state = 'playing'
        self.interactions[interaction.user.id] = interaction
        await self.refresh()
        failed = 0
        for user_id in self.players:
            target = self.interactions.get(user_id)
            if target is None:
                failed += 1
                continue
            try:
                await self.send_card(target, user_id)
            except discord.HTTPException:
                failed += 1
        message = 'Runda wystartowała — każdy dostał prywatną kartę.'
        if failed:
            message += f' {failed} os. nie dostało karty — niech klikną „Pokaż moją cyfrę”.'
        await reply(interaction, message)

    async def action(self, interaction, action):
        user_id = interaction.user.id
        if action == 'card':
            if self.state != 'playing' or user_id not in self.players:
                await reply(interaction, 'Nie bierzesz udziału w tej rundzie.')
                return
            await interaction.response.defer(ephemeral=True)
            self.interactions[user_id] = interaction
            await self.send_card(interaction, user_id)
            return
        if action == 'join':
            await interaction.response.defer(ephemeral=True)
            async with self.lock:
                if self.state != 'waiting':
                    await reply(interaction, 'Gra już trwa.')
                elif user_id in self.players:
                    await reply(interaction, 'Jesteś już w lobby.')
                elif len(self.players) >= MAX_PLAYERS:
                    await reply(interaction, 'Lobby jest pełne.')
                else:
                    self.players.append(user_id)
                    self.interactions[user_id] = interaction
                    await self.refresh()
                    await reply(interaction, 'Dołączono do lobby!')
            return
        if action == 'leave':
            await interaction.response.defer(ephemeral=True)
            async with self.lock:
                if self.state != 'waiting' or user_id not in self.players or user_id == self.owner_id:
                    await reply(interaction, 'Nie możesz teraz opuścić lobby.')
                    return
                self.players.remove(user_id)
                self.interactions.pop(user_id, None)
                await self.refresh()
                await reply(interaction, 'Opuszczono lobby.')
            return
        if user_id != self.owner_id:
            await reply(interaction, 'Tylko twórca lobby może to zrobić.')
            return
        if action == 'reveal':
            if self.state != 'playing' or self.revealed:
                await reply(interaction, 'Nie ma czego pokazać.')
                return
            self.revealed = True
            await interaction.response.send_message(
                f'❓ **Cyfry nie znał(a):** <@{self.impostor_id}> — cyfra to **{self.number}**.',
                allowed_mentions=NO_MENTIONS)
            await self.refresh()
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            if action in ('start', 'next'):
                if action == 'start' and self.state != 'waiting':
                    await reply(interaction, 'Gra już trwa.')
                elif len(self.players) < MIN_PLAYERS:
                    await reply(interaction, f'Potrzeba co najmniej {MIN_PLAYERS} graczy.')
                else:
                    await self.new_round(interaction)
            elif action == 'end':
                self.state = 'finished'
                await self.refresh()
                self.close()
                await reply(interaction, 'Gra zakończona.')


class CyfryButton(discord.ui.Button):
    def __init__(self, game, action, label, style, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.game = game
        self.action_name = action

    async def callback(self, interaction):
        await self.game.action(interaction, self.action_name)


class CyfryView(discord.ui.LayoutView):
    def __init__(self, game):
        super().__init__(timeout=LOBBY_TIMEOUT)
        self.game = game
        self.render()

    def render(self):
        self.clear_items()
        game = self.game
        title, status = {
            'waiting': ('🔢 Lobby Cyfr', 'Dołącz przyciskiem poniżej. Potrzeba minimum 3 graczy.'),
            'playing': (f'🔢 Cyfry — runda {game.round}', f'Zaczyna <@{game.starter_id}>.'),
            'finished': ('🔢 Cyfry — koniec gry', 'Dzięki za grę!'),
            'expired': ('🔢 Lobby Cyfr wygasło', 'Brak aktywności przez godzinę.'),
        }[game.state]
        container = discord.ui.Container(accent_color=0x5865F2 if game.state in ('waiting', 'playing') else 0x747F8D)
        container.add_item(discord.ui.TextDisplay(
            f'# {title}\n{status}\n**Twórca:** <@{game.owner_id}>\n'
            f'**Gracze:** `{len(game.players)}/{MAX_PLAYERS}`\n'
            '-# Wszyscy dostają tę samą cyfrę 1–10, oprócz jednej osoby, która jej nie zna.'
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('### Gracze\n' + '\n'.join(f'👤 <@{uid}>' for uid in game.players)))
        container.add_item(discord.ui.Separator())
        if game.state == 'waiting':
            container.add_item(discord.ui.ActionRow(
                CyfryButton(game, 'join', 'Dołącz', discord.ButtonStyle.success,
                            disabled=len(game.players) >= MAX_PLAYERS),
                CyfryButton(game, 'leave', 'Wyjdź', discord.ButtonStyle.secondary),
                CyfryButton(game, 'start', 'Start', discord.ButtonStyle.primary,
                            disabled=len(game.players) < MIN_PLAYERS),
                CyfryButton(game, 'end', 'Anuluj', discord.ButtonStyle.danger),
            ))
        elif game.state == 'playing':
            container.add_item(discord.ui.ActionRow(
                CyfryButton(game, 'card', 'Pokaż moją cyfrę', discord.ButtonStyle.primary),
                CyfryButton(game, 'reveal', 'Kto nie znał cyfry?', discord.ButtonStyle.secondary,
                            disabled=game.revealed),
                CyfryButton(game, 'next', 'Nowa runda', discord.ButtonStyle.success),
                CyfryButton(game, 'end', 'Zakończ', discord.ButtonStyle.danger),
            ))
            container.add_item(discord.ui.TextDisplay('-# Ujawnienie, nowa runda i zakończenie: tylko twórca'))
        self.add_item(container)

    async def on_timeout(self):
        game = self.game
        if game.state in ('waiting', 'playing'):
            game.state = 'expired'
            self.render()
            if game.message:
                try:
                    await game.message.edit(view=self, allowed_mentions=NO_MENTIONS)
                except discord.HTTPException:
                    pass
        if LOBBIES.get(game.key) is game:
            del LOBBIES[game.key]

    async def on_error(self, interaction, error, item):
        logger.error('Cyfry panel failed', exc_info=(type(error), error, error.__traceback__))
        try:
            await reply(interaction, 'Coś poszło nie tak. Spróbuj ponownie.')
        except discord.HTTPException:
            pass


async def setup_cyfry_commands(client, tree, guild_id=None):
    guild = discord.Object(id=guild_id) if guild_id else None

    @tree.command(name='cyfry', description='Gra w cyfry: jedna osoba nie zna wylosowanej cyfry 1–10', guild=guild)
    @app_commands.guild_only()
    async def cyfry(interaction: discord.Interaction):
        key = (interaction.guild_id, interaction.channel_id)
        existing = LOBBIES.get(key)
        if existing:
            link = f' {existing.message.jump_url}' if existing.message else ''
            await reply(interaction, f'Na tym kanale trwa już gra w cyfry.{link}')
            return
        game = CyfryGame(key, interaction.user.id, interaction)
        LOBBIES[key] = game
        try:
            await interaction.response.send_message(view=game.view, allowed_mentions=NO_MENTIONS)
            game.message = await interaction.original_response()
        except BaseException:
            game.close()
            raise
