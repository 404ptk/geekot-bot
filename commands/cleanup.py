"""Preview and explicitly confirm user-specific message cleanup."""
import asyncio
from collections import Counter, defaultdict
from datetime import timedelta
import logging

import discord
from config.conf_permissions_settings import can_use_moderator_command, require_moderator_command

logger = logging.getLogger(__name__)
MENTIONS = discord.AllowedMentions.none()
PERIODS = {'hour': ('Ostatnia godzina', timedelta(hours=1)),
           'day': ('Ostatni dzień', timedelta(days=1)),
           'week': ('Ostatni tydzień', timedelta(days=7)),
           'month': ('Ostatnie 30 dni', timedelta(days=30))}
PAGE_SIZE = 12
OPERATION_TIMEOUT = 600


async def reply(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=MENTIONS)


def accessible(channel, actor, bot, *, deleting=False):
    for member in (actor, bot):
        permissions = channel.permissions_for(member)
        if not permissions.view_channel or not permissions.read_message_history:
            return False
        if deleting and not permissions.manage_messages:
            return False
    return True


async def find_messages(channels, actor, bot, target_id, *, count=None, after=None, before=None):
    """Newest N means N across the entire selected scope, never N per channel."""
    matches, skipped = [], []
    for channel in channels:
        if not accessible(channel, actor, bot, deleting=True):
            skipped.append((channel.id, 'brak dostępu do historii lub zarządzania wiadomościami'))
            continue
        found = []
        try:
            async for message in channel.history(limit=None, after=after, before=before, oldest_first=False):
                if message.author.id != target_id:
                    continue
                found.append(message)
                if count is not None and len(found) >= count:
                    break
        except discord.HTTPException:
            skipped.append((channel.id, 'nie udało się odczytać historii'))
            # A failed channel cannot contribute a complete, trustworthy selection.
            continue
        matches.extend(found)
        if count is not None:
            matches.sort(key=lambda message: message.id, reverse=True)
            del matches[count:]
    matches.sort(key=lambda message: message.id, reverse=True)
    return matches, skipped


async def delete_snapshot(messages, actor, bot, reason, progress=None):
    """Delete only the exact message IDs shown in the preview."""
    groups = defaultdict(list)
    for message in messages:
        groups[message.channel.id].append(message)
    deleted, missing, failed = Counter(), Counter(), Counter()
    for channel_id, group in groups.items():
        channel = group[0].channel
        if not accessible(channel, actor, bot, deleting=True):
            failed[channel_id] += len(group)
            continue
        recent, old = [], []
        # Margin avoids messages crossing Discord's 14-day bulk boundary in flight.
        cutoff = discord.utils.utcnow() - timedelta(days=14) + timedelta(minutes=5)
        for message in group:
            (recent if message.created_at > cutoff else old).append(message)
        for offset in range(0, len(recent), 100):
            batch = recent[offset:offset + 100]
            if len(batch) == 1:
                old.extend(batch)
                continue
            try:
                await channel.delete_messages(batch, reason=reason)
                deleted[channel_id] += len(batch)
            except discord.Forbidden:
                failed[channel_id] += len(batch)
            except discord.HTTPException:
                # Retry individually: an age boundary or one missing ID must not
                # make the report claim that the whole batch was deleted.
                old.extend(batch)
            if progress:
                await progress(deleted, missing, failed)
        for message in old:
            try:
                await message.delete(reason=reason)
                deleted[channel_id] += 1
            except discord.NotFound:
                missing[channel_id] += 1
            except discord.HTTPException:
                failed[channel_id] += 1
            if progress:
                await progress(deleted, missing, failed)
    return deleted, missing, failed


class CleanupButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction):
        await self.view.action(interaction, self.action)


class CleanupSelect(discord.ui.Select):
    def __init__(self, kind, options, value, disabled=False):
        super().__init__(placeholder='Zakres' if kind == 'scope' else 'Tryb', disabled=disabled,
                         options=[discord.SelectOption(label=label, value=key, default=key == value)
                                  for key, label in options])
        self.kind = kind

    async def callback(self, interaction):
        panel = self.view
        async with panel.lock:
            if panel.busy or panel.expired:
                await reply(interaction, 'Poczekaj na zakończenie operacji lub otwórz nowy panel.')
                return
            setattr(panel, self.kind, self.values[0])
            panel.invalidate()
            panel.render()
            await interaction.response.edit_message(view=panel, allowed_mentions=MENTIONS)


class CleanupChannels(discord.ui.ChannelSelect):
    def __init__(self, panel):
        super().__init__(placeholder='Wybierz kanały (łącznie, nie osobno)', min_values=1, max_values=25,
                         channel_types=[discord.ChannelType.text, discord.ChannelType.news,
                                        discord.ChannelType.forum],
                         default_values=panel.selected_channels, disabled=panel.busy or panel.expired)

    async def callback(self, interaction):
        panel = self.view
        async with panel.lock:
            if panel.busy or panel.expired:
                await reply(interaction, 'Panel jest zajęty lub wygasł.')
                return
            panel.selected_channels = [panel.guild.get_channel(value.id) for value in self.values]
            panel.selected_channels = [channel for channel in panel.selected_channels if channel is not None]
            panel.invalidate()
            panel.render()
            await interaction.response.edit_message(view=panel, allowed_mentions=MENTIONS)


class CleanupModal(discord.ui.Modal):
    def __init__(self, panel, custom_period=False):
        super().__init__(title='Własny okres' if custom_period else 'Liczba wiadomości', timeout=300)
        self.panel = panel
        self.custom_period = custom_period
        if custom_period:
            self.start = discord.ui.TextInput(label='Od (UTC): RRRR-MM-DD GG:MM', placeholder='2026-10-01 00:00', max_length=16)
            self.end = discord.ui.TextInput(label='Do (UTC): RRRR-MM-DD GG:MM', required=False, max_length=16)
            self.add_item(self.start)
            self.add_item(self.end)
        else:
            self.number = discord.ui.TextInput(label='Ile ostatnich wiadomości? (łącznie)',
                default=str(panel.count) if panel.count else None, min_length=1, max_length=7)
            self.add_item(self.number)

    async def on_submit(self, interaction):
        if not await self.panel.interaction_check(interaction):
            return
        async with self.panel.lock:
            if self.panel.busy or self.panel.expired:
                await reply(interaction, 'Panel jest zajęty lub wygasł.')
                return
            try:
                if self.custom_period:
                    from datetime import datetime, timezone
                    start = datetime.strptime(self.start.value.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                    end = (datetime.strptime(self.end.value.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                           if self.end.value.strip() else discord.utils.utcnow())
                    if start >= end or end > discord.utils.utcnow():
                        raise ValueError
                    self.panel.custom_period = (start, end)
                    self.panel.mode = 'custom'
                else:
                    count = int(self.number.value.strip())
                    if count < 1:
                        raise ValueError
                    self.panel.count = count
                    self.panel.mode = 'last'
            except ValueError:
                await reply(interaction, 'Podaj poprawny okres UTC (od < do ≤ teraz).' if self.custom_period else 'Podaj dodatnią liczbę całkowitą.')
                return
            self.panel.invalidate()
            self.panel.render()
            await interaction.response.edit_message(view=self.panel, allowed_mentions=MENTIONS)


class CleanupPanel(discord.ui.LayoutView):
    def __init__(self, owner_id, guild, current_channel, target):
        super().__init__(timeout=900)
        self.owner_id, self.guild, self.current_channel, self.target = owner_id, guild, current_channel, target
        self.scope, self.mode, self.count = 'here', 'last', None
        self.selected_channels = []
        self.custom_period = None
        self.message = None
        self.matches = None
        self.skipped = []
        self.report = None
        self.notice = ''
        self.busy = False
        self.expired = False
        self.page = 0
        self.lock = asyncio.Lock()
        self.task = None
        self.last_progress = 0
        self.render()

    def invalidate(self):
        self.matches, self.report = None, None
        self.skipped, self.page, self.notice = [], 0, ''

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await reply(interaction, 'Otwórz własny panel przez /wyczysc osoba.')
            return False
        if self.expired or self.is_finished():
            await reply(interaction, 'Panel wygasł. Otwórz nowy przez /wyczysc osoba.')
            return False
        if not can_use_moderator_command(interaction.user, interaction.guild, 'wyczysc'):
            await reply(interaction, 'Nie masz uprawnień do tej operacji.')
            return False
        return True

    def render(self):
        self.clear_items()
        container = discord.ui.Container(accent_color=0xE74C3C)
        container.add_item(discord.ui.TextDisplay(
            f'# 🧹 Czyszczenie wiadomości\n**Osoba:** <@{self.target.id}>\n'
            'Wybierz zakres i tryb. Wyszukanie tworzy podgląd; usuwanie wymaga potwierdzenia.\n'
            'Zakres wielu kanałów obejmuje też dostępne wątki, w tym archiwalne.'))
        disabled = self.busy or self.expired
        container.add_item(discord.ui.ActionRow(CleanupSelect('scope', [
            ('here', 'Obecny kanał'), ('selected', 'Wybrane kanały'), ('server', 'Cały serwer')], self.scope, disabled)))
        if self.scope == 'selected':
            container.add_item(discord.ui.ActionRow(CleanupChannels(self)))
        container.add_item(discord.ui.ActionRow(CleanupSelect('mode', [
            ('last', 'Ostatnie N wiadomości'), *[(key, value[0]) for key, value in PERIODS.items()],
            ('custom', 'Własny okres (UTC)'), ('all', 'Cała historia tej osoby')], self.mode, disabled)))
        if self.mode == 'last':
            container.add_item(discord.ui.TextDisplay(f'**Liczba łącznie:** {self.count or "nie ustawiono — wpisz liczbę"}'))
            container.add_item(discord.ui.ActionRow(CleanupButton('count', 'Wpisz liczbę', disabled=disabled)))
        elif self.mode == 'custom':
            text = 'nie ustawiono' if not self.custom_period else f'{self.custom_period[0]:%Y-%m-%d %H:%M} → {self.custom_period[1]:%Y-%m-%d %H:%M} UTC'
            container.add_item(discord.ui.TextDisplay(f'**Okres:** {text}'))
            container.add_item(discord.ui.ActionRow(CleanupButton('period', 'Wpisz okres', disabled=disabled)))
        elif self.mode == 'all':
            container.add_item(discord.ui.TextDisplay('**Cała historia:** wszystkie znalezione wiadomości tej osoby w wybranym zakresie.'))
        container.add_item(discord.ui.ActionRow(CleanupButton('search', 'Wyszukaj', discord.ButtonStyle.primary, disabled)))
        rows = []
        if self.report is not None:
            deleted, missing, failed = self.report
            container.add_item(discord.ui.TextDisplay(
                f'### Wynik\nUsunięto: **{sum(deleted.values())}** · już nie istnieją: **{sum(missing.values())}** · nie usunięto: **{sum(failed.values())}**'))
            rows = [f'<#{key}> — usunięto {deleted[key]}, brak {missing[key]}, nie usunięto {failed[key]}'
                    for key in sorted(set(deleted) | set(missing) | set(failed))]
        elif self.matches is not None:
            counts = Counter(message.channel.id for message in self.matches)
            container.add_item(discord.ui.TextDisplay(f'### Podgląd\nZnaleziono **{len(self.matches)}** wiadomości na **{len(counts)}** kanałach.'))
            rows = [f'<#{key}> — {value} wiadomości' for key, value in counts.items()]
            container.add_item(discord.ui.ActionRow(
                CleanupButton('delete', f'Usuń {len(self.matches)} wiadomości', discord.ButtonStyle.danger, disabled or not self.matches),
                CleanupButton('back', 'Wróć do ustawień', disabled=disabled)))
        rows.extend(f'<#{key}> — pominięto: {reason}' for key, reason in self.skipped)
        if rows:
            pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
            self.page = max(0, min(self.page, pages - 1))
            container.add_item(discord.ui.TextDisplay('\n'.join(rows[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE])))
            if pages > 1:
                container.add_item(discord.ui.TextDisplay(f'-# Strona {self.page + 1}/{pages}'))
                container.add_item(discord.ui.ActionRow(
                    CleanupButton('previous', 'Poprzednia', disabled=disabled or self.page == 0),
                    CleanupButton('next', 'Następna', disabled=disabled or self.page == pages - 1)))
        if self.notice:
            container.add_item(discord.ui.TextDisplay(self.notice))
        container.add_item(discord.ui.TextDisplay('-# Panel prywatny • dostęp sprawdzany ponownie przed usunięciem • starsze wiadomości są usuwane pojedynczo'))
        self.add_item(container)

    async def update(self):
        self.render()
        if self.message:
            await self.message.edit(view=self, allowed_mentions=MENTIONS)

    async def channels(self, actor):
        if self.scope == 'here':
            return [self.current_channel], []
        parents = self.selected_channels if self.scope == 'selected' else [*self.guild.text_channels, *self.guild.forums]
        found, skipped = {}, []
        try:
            active_threads = await self.guild.active_threads()
        except discord.HTTPException:
            active_threads = self.guild.threads
            skipped.append((self.current_channel.id, 'nie udało się pobrać pełnej listy aktywnych wątków'))
        for parent in parents:
            if isinstance(parent, discord.TextChannel):
                found[parent.id] = parent
            if not accessible(parent, actor, self.guild.me):
                skipped.append((parent.id, 'brak dostępu do kanału i jego wątków'))
                continue
            for thread in active_threads:
                if thread.parent_id == parent.id:
                    found[thread.id] = thread
            options = [{}]
            if isinstance(parent, discord.TextChannel):
                options.append({'private': True})
            for kwargs in options:
                try:
                    async for thread in parent.archived_threads(limit=None, **kwargs):
                        found[thread.id] = thread
                except discord.HTTPException:
                    skipped.append((parent.id, 'nie udało się pobrać ' + ('prywatnych ' if kwargs else '') + 'archiwalnych wątków'))
        return list(found.values()), skipped

    async def search(self, actor):
        before = discord.utils.utcnow()
        after = before - PERIODS[self.mode][1] if self.mode in PERIODS else None
        if self.mode == 'custom':
            after, before = self.custom_period
        channels, skipped = await self.channels(actor)
        self.matches, failures = await find_messages(channels, actor, self.guild.me, self.target.id,
            count=self.count if self.mode == 'last' else None, after=after, before=before)
        self.skipped = skipped + failures
        self.notice = 'Podgląd obejmuje tylko odczytane kanały. Pominięte miejsca nie będą czyszczone.' if self.skipped else ''

    async def delete(self, actor):
        # Settings and messages stay frozen while busy. No history query here.
        actor = await self.guild.fetch_member(self.owner_id)
        if not can_use_moderator_command(actor, self.guild, 'wyczysc'):
            raise PermissionError('Odebrano uprawnienia do czyszczenia.')
        async def progress(deleted, missing, failed):
            self.report = (deleted.copy(), missing.copy(), failed.copy())
            if asyncio.get_running_loop().time() - self.last_progress > 5:
                self.last_progress = asyncio.get_running_loop().time()
                try:
                    await self.update()
                except discord.HTTPException:
                    logger.warning('Could not display cleanup progress', exc_info=True)
        self.report = await delete_snapshot(self.matches, actor, self.guild.me,
            f'/wyczysc: wykonujący {actor.id}, autor wiadomości {self.target.id}', progress)
        self.matches = None
        self.notice = 'Czyszczenie zakończone. Nowe wiadomości wysłane po podglądzie pozostały na kanale.'
        logger.info('Cleanup guild=%s actor=%s target=%s deleted=%s', self.guild.id, actor.id, self.target.id,
                    sum(self.report[0].values()) if self.report else 0)

    async def run(self, operation, actor):
        try:
            await asyncio.wait_for(self.search(actor) if operation == 'search' else self.delete(actor), OPERATION_TIMEOUT)
        except asyncio.TimeoutError:
            self.notice = 'Operacja przekroczyła 10 minut i została przerwana. Zawęź zakres lub okres.'
            if operation == 'search':
                self.matches = None
        except Exception:
            logger.exception('Cleanup operation failed: %s', operation)
            self.notice = 'Nie udało się zakończyć operacji. Sprawdź uprawnienia i wyszukaj ponownie.'
            if operation == 'search':
                self.matches = None
        finally:
            if operation == 'delete' and self.matches is not None:
                # Include unprocessed messages if an operation was interrupted.
                totals = Counter(message.channel.id for message in self.matches)
                deleted, missing, failed = self.report or (Counter(), Counter(), Counter())
                for key, total in totals.items():
                    failed[key] = total - deleted[key] - missing[key]
                self.report = deleted, missing, failed
                self.matches = None
            self.busy = False
            try:
                await self.update()
            except discord.HTTPException:
                logger.warning('Cleanup panel could not be updated', exc_info=True)

    async def action(self, interaction, action):
        async with self.lock:
            if self.busy or self.expired:
                await reply(interaction, 'Poczekaj na zakończenie operacji lub otwórz nowy panel.')
                return
            if action in ('count', 'period'):
                await interaction.response.send_modal(CleanupModal(self, action == 'period'))
                return
            if action in ('previous', 'next'):
                self.page += -1 if action == 'previous' else 1
            elif action == 'back':
                self.invalidate()
            elif action in ('search', 'delete'):
                if action == 'delete' and (not self.matches or self.report is not None):
                    await reply(interaction, 'Najpierw wyszukaj wiadomości i sprawdź podgląd.')
                    return
                if action == 'search':
                    if (self.mode == 'last' and not self.count) or (self.mode == 'custom' and not self.custom_period):
                        await reply(interaction, 'Najpierw wpisz liczbę wiadomości lub okres.')
                        return
                    if self.scope == 'selected' and not self.selected_channels:
                        await reply(interaction, 'Wybierz przynajmniej jeden kanał.')
                        return
                    self.invalidate()
                self.busy = True
                self.notice = 'Przeszukuję historię — niczego jeszcze nie usuwam.' if action == 'search' else 'Usuwam wyłącznie wiadomości z podglądu…'
                try:
                    await interaction.response.defer()
                    await self.update()
                except Exception:
                    self.busy = False
                    raise
                self.task = asyncio.create_task(self.run(action, interaction.user))
                return
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=MENTIONS)

    async def on_timeout(self):
        self.expired = True
        try:
            await self.update()
        except discord.HTTPException:
            pass

    async def on_error(self, interaction, error, item):
        logger.error('Cleanup panel failed', exc_info=(type(error), error, error.__traceback__))
        await reply(interaction, 'Nie udało się wykonać operacji. Otwórz panel ponownie.')


def register_cleanup_command(tree, guild):
    @tree.command(name='wyczysc', description='Panel usuwania wiadomości jednej osoby z jednego lub wielu kanałów', guild=guild)
    @discord.app_commands.guild_only()
    @discord.app_commands.describe(osoba='Osoba, której wiadomości chcesz usunąć')
    async def wyczysc(interaction: discord.Interaction, osoba: discord.User):
        if not await require_moderator_command(interaction, 'wyczysc'):
            return
        if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await reply(interaction, 'Otwórz tę komendę na kanale tekstowym lub w wątku.')
            return
        panel = CleanupPanel(interaction.user.id, interaction.guild, interaction.channel, osoba)
        await interaction.response.send_message(view=panel, ephemeral=True, allowed_mentions=MENTIONS)
        panel.message = await interaction.original_response()
