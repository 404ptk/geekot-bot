"""Administrator health dashboard in /config."""
import math

import discord

from bot_health import load_settings, monitor, process_stats, update_settings
from config.conf_panel import ConfigView, PanelContent

LABELS = {'ok': '🟢 Działa', 'warning': '🟡 Nieudana próba', 'error': '🔴 Błędy',
          'stale': '🔴 Brak zakończenia w terminie', 'waiting': '⚪ Oczekiwanie',
          'disabled': '⏸️ Wstrzymane', 'running': '🔵 W trakcie'}


def timestamp(value):
    return f'<t:{int(value)}:R>' if value else 'brak pomiaru'


def clean(value, limit=1000):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


class ModuleSelect(discord.ui.Select):
    def __init__(self, modules):
        super().__init__(placeholder='Szczegóły modułu…', options=[
            discord.SelectOption(label=s.label, value=s.key) for s in modules])

    async def callback(self, interaction):
        view = ModuleHealthView(self.view.owner_id, self.values[0], self.view.page)
        await self.view.show(interaction, view, view.content())


class HealthView(ConfigView):
    def __init__(self, owner_id, page=0):
        super().__init__(owner_id)
        modules = list(monitor.modules.values())
        self.pages = max(1, (len(modules) + 4) // 5)
        self.page = min(max(0, page), self.pages - 1)
        self.modules = modules[self.page * 5:(self.page + 1) * 5]
        if self.modules:
            self.add_item(ModuleSelect(self.modules))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == self.pages - 1

    def content(self):
        uptime, rss = process_stats()
        hours, minutes = divmod(uptime // 60, 60)
        days, hours = divmod(hours, 24)
        latency = getattr(monitor.client, 'latency', float('nan'))
        ping = f'{latency * 1000:.0f} ms' if math.isfinite(latency) else 'brak połączenia / pomiaru'
        ram = f'{rss:.1f} MB' if rss is not None else 'pomiar niedostępny'
        content = PanelContent(title='🤖 Stan bota', description=(
            f'Działa od: **{days} dni, {hours} godz., {minutes} min**\n'
            f'RAM procesu: **{ram}** · opóźnienie Discorda: **{ping}**'))
        for state in self.modules:
            text = f'{LABELS[state.status()]}\nOstatni sukces: {timestamp(state.last_success)}\nOstatnie sprawdzenie: {timestamp(state.last_check)}'
            reason = state.disabled_reason() or state.skip_reason
            if reason:
                text += f'\n{clean(reason, 150)}'
            if state.failures:
                text += f'\nKolejne niepowodzenia: {state.failures}'
            content.add_field(name=state.label, value=text)
        if not self.modules:
            content.add_field(name='Moduły', value='Trwa inicjalizacja. Odśwież za chwilę.')
        settings = load_settings()
        alerts = f"<#{settings['channel_id']}> · próg: {settings['threshold']}" if settings['enabled'] else 'wyłączone'
        content.add_field(name='Alerty', value=alerts)
        if monitor.storage_error or monitor.alert_error:
            content.add_field(name='Wynik', value='Nie udało się zapisać historii lub dostarczyć alertu. Sprawdź dostęp do dysku i kanału.')
        content.set_footer(text=f'Strona {self.page + 1}/{self.pages} • odświeżanie przyciskiem • RAM dotyczy procesu bota. Historia: ostatnie 20 prób modułu.')
        return content

    @discord.ui.button(label='Odśwież', row=1, style=discord.ButtonStyle.primary)
    async def refresh(self, interaction, button):
        view = HealthView(self.owner_id, self.page)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Ustawienia alertów', row=1)
    async def alerts(self, interaction, button):
        view = AlertSettingsView(self.owner_id)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Poprzednia', row=2)
    async def previous(self, interaction, button):
        view = HealthView(self.owner_id, self.page - 1)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Następna', row=2)
    async def next_page(self, interaction, button):
        view = HealthView(self.owner_id, self.page + 1)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Wróć do konfiguracji', row=3)
    async def home(self, interaction, button):
        from config.conf_menu import ConfigHomeView
        view = ConfigHomeView(self.owner_id)
        await self.show(interaction, view, view.content())


class ModuleHealthView(ConfigView):
    def __init__(self, owner_id, key, page=0, history_page=0):
        super().__init__(owner_id)
        self.key, self.page = key, page
        state = monitor.modules[key]
        self.history_pages = max(1, (len(state.history) + 4) // 5)
        self.history_page = min(max(0, history_page), self.history_pages - 1)
        self.older.disabled = self.history_page == self.history_pages - 1
        self.newer.disabled = self.history_page == 0
        self.retry.disabled = not state.retry or state.lock.locked() or state.pending or bool(state.disabled_reason())

    def content(self, notice=None):
        s = monitor.modules[self.key]
        content = PanelContent(title=f'🤖 {s.label}', description=LABELS[s.status()])
        next_check = timestamp(s.next_check()) if not s.disabled_reason() and s.started is None else '—'
        duration = f'{s.duration:.2f} s' if s.duration is not None else 'brak pomiaru'
        content.add_field(name='Pomiary', value=(
            f'Ostatnia próba wykonania: {timestamp(s.last_attempt)}\n'
            f'Ostatni sukces: {timestamp(s.last_success)}\n'
            f'Następne planowe sprawdzenie: około {next_check}\n'
            f'Czas ostatniej próby: {duration}\nKolejne niepowodzenia: {s.failures}'))
        if s.disabled_reason() or s.skip_reason:
            content.add_field(name='Harmonogram', value=clean(s.disabled_reason() or s.skip_reason))
        if s.error:
            content.add_field(name='Ostatni błąd', value=clean(s.error, 1800))
        history = []
        entries = list(reversed(s.history))[self.history_page * 5:(self.history_page + 1) * 5]
        for entry in entries:
            text = f"{'🟢' if entry['ok'] else '🔴'} {timestamp(entry['at'])} · {entry['duration']:.2f} s"
            if entry.get('error'):
                text += '\n' + clean(entry['error'], 180)
            history.append(text)
        content.add_field(name=f'Historia — strona {self.history_page + 1}/{self.history_pages}', value='\n'.join(history) or 'Brak prób.')
        if notice:
            content.add_field(name='Wynik', value=notice)
        content.set_footer(text='Błędy zawierają typ, kod HTTP i miejsca w kodzie. Treść wyjątków, klucze i dane żądań nie są zapisywane. Ponów przestrzega harmonogramu publikacji.')
        return content

    @discord.ui.button(label='Odśwież', style=discord.ButtonStyle.primary)
    async def refresh(self, interaction, button):
        view = ModuleHealthView(self.owner_id, self.key, self.page, self.history_page)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Ponów pobieranie')
    async def retry(self, interaction, button):
        notice = monitor.launch_retry(self.key)
        view = ModuleHealthView(self.owner_id, self.key, self.page, self.history_page)
        await self.show(interaction, view, view.content(notice))

    @discord.ui.button(label='Nowsze próby', row=1)
    async def newer(self, interaction, button):
        view = ModuleHealthView(self.owner_id, self.key, self.page, self.history_page - 1)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Starsze próby', row=1)
    async def older(self, interaction, button):
        view = ModuleHealthView(self.owner_id, self.key, self.page, self.history_page + 1)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Wróć do stanu bota', row=1)
    async def back(self, interaction, button):
        view = HealthView(self.owner_id, self.page)
        await self.show(interaction, view, view.content())


class ThresholdModal(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title='Próg alertów', timeout=600)
        self.panel = panel
        self.threshold = discord.ui.TextInput(label='Liczba kolejnych nieudanych prób (1–20)',
                                             default=str(load_settings()['threshold']), max_length=2)
        self.add_item(self.threshold)

    async def on_submit(self, interaction):
        if not await self.panel.interaction_check(interaction):
            return
        if self.panel.is_finished():
            await interaction.response.send_message('Panel wygasł. Otwórz /config ponownie.', ephemeral=True)
            return
        try:
            update_settings(threshold=int(self.threshold.value))
        except ValueError:
            await interaction.response.send_message('Podaj liczbę od 1 do 20.', ephemeral=True)
            return
        view = AlertSettingsView(self.panel.owner_id)
        await self.panel.show(interaction, view, view.content())

    async def on_error(self, interaction, error):
        await self.panel.on_error(interaction, error, None)


class AlertSettingsView(ConfigView):
    def content(self):
        settings = load_settings()
        content = PanelContent(title='🔔 Alerty stanu bota', description=(
            'Powiadomienie po przekroczeniu progu błędów lub opóźnieniu zadania; kolejne dopiero po odzyskaniu sprawności. '
            'Zatrzymanie całego procesu wymaga zewnętrznego monitora.'))
        content.add_field(name='Stan', value='Włączone' if settings['enabled'] else 'Wyłączone')
        content.add_field(name='Kanał', value=f"<#{settings['channel_id']}>" if settings['channel_id'] else 'Nie wybrano')
        content.add_field(name='Próg', value=f"{settings['threshold']} kolejne nieudane próby", action='threshold')
        return content

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder='Kanał alertów…')
    async def channel(self, interaction, select):
        channel = interaction.guild.get_channel(select.values[0].id)
        if channel is None:
            await interaction.response.send_message('Nie można odczytać kanału.', ephemeral=True)
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message('Bot potrzebuje dostępu do kanału i wysyłania wiadomości.', ephemeral=True)
            return
        update_settings(channel_id=channel.id)
        view = AlertSettingsView(self.owner_id)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Włącz / wyłącz alerty', row=1)
    async def toggle(self, interaction, button):
        settings = load_settings()
        if not settings['enabled'] and not settings['channel_id']:
            await interaction.response.send_message('Najpierw wybierz kanał alertów.', ephemeral=True)
            return
        update_settings(enabled=not settings['enabled'])
        view = AlertSettingsView(self.owner_id)
        await self.show(interaction, view, view.content())

    @discord.ui.button(label='Zmień próg', row=1)
    async def threshold(self, interaction, button):
        await interaction.response.send_modal(ThresholdModal(self))

    @discord.ui.button(label='Wróć do stanu bota', row=2)
    async def back(self, interaction, button):
        view = HealthView(self.owner_id)
        await self.show(interaction, view, view.content())
