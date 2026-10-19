"""Single Components V2 configuration panel for job offers."""
import copy
import json
import logging
import uuid

import discord
from discord import app_commands

from jobs.config import load_config, save_config
from jobs.constants import DEFAULT_CONFIG, GUILD_ID, ALL_LEVELS, LEVEL_LABELS, MIN_INTERVAL_MINUTES
from jobs.filters import filters_summary, normalize_allowed_levels
from jobs.permissions import has_high_tier_guard
from jobs.providers.custom import validate_provider

log = logging.getLogger(__name__)
MENTIONS = discord.AllowedMentions.none()


async def reply(interaction, text):
    sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await sender(text, ephemeral=True, allowed_mentions=MENTIONS)


class PanelButton(discord.ui.Button):
    def __init__(self, action, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(label=label, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction):
        await self.view.action(interaction, self.action)


class PanelSelect(discord.ui.Select):
    def __init__(self, kind, options, **kwargs):
        super().__init__(options=options, **kwargs)
        self.kind = kind

    async def callback(self, interaction):
        panel = self.view
        if self.kind == 'api':
            panel.selected = self.values[0]
            panel.confirm_delete = False
        else:
            config = load_config()
            config['filters']['allowed_levels'] = self.values
            save_config(config)
        await panel.refresh(interaction)


class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Zmień kanał ofert', channel_types=[discord.ChannelType.text])

    async def callback(self, interaction):
        channel = self.values[0]
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links and permissions.attach_files):
            await reply(interaction, 'Bot potrzebuje na tym kanale dostępu oraz wysyłania wiadomości, embedów i plików.')
            return
        config = load_config()
        config['discord_channel_id'] = channel.id
        save_config(config)
        await self.view.refresh(interaction)


class SettingsModal(discord.ui.Modal):
    def __init__(self, panel, kind, provider=None):
        super().__init__(title={'filters': 'Filtry ofert', 'schedule': 'Częstotliwość sprawdzania', 'api': 'Konfiguracja API'}[kind], timeout=600)
        self.panel, self.kind = panel, kind
        self.original = copy.deepcopy(provider)
        config = load_config()
        self.inputs = {}
        if kind == 'filters':
            fields = [('location_city', 'Miasto (puste = dowolne)', config['filters'].get('location_city', '')),
                      ('offer_source', 'Źródło (np. justjoin.it; puste = wszystkie)', config['filters'].get('offer_source', '')),
                      ('offer_category', 'Kategoria (puste = wszystkie)', config['filters'].get('offer_category', '')),
                      ('search', 'Szukana fraza (puste = brak)', config['filters'].get('search', '')),
                      ('offer_status', 'Status: active lub expired', config['filters'].get('offer_status', 'active'))]
        elif kind == 'schedule':
            fields = [('interval_minutes', f'Interwał w minutach (minimum {MIN_INTERVAL_MINUTES})', str(config['interval_minutes']))]
        else:
            p = provider or {'name': '', 'type': 'json', 'url': '', 'options': {
                'data_path': 'data', 'page_param': 'page', 'headers': {}, 'params': {},
                'fields': {'offer_uuid': 'id', 'offer_title': 'title', 'offer_href': 'url', 'offer_city': 'city'}}}
            fields = [('name', 'Nazwa API', p['name']), ('type', 'Typ: isitfair, olx lub json', p['type']),
                      ('url', 'Adres API (HTTPS)', p['url']),
                      ('options', 'Opcje JSON: headers, params, data_path, fields', json.dumps(p.get('options', {}), ensure_ascii=False, indent=2))]
        for key, label, value in fields:
            field = discord.ui.TextInput(label=label, default=str(value), required=kind != 'filters' or key == 'offer_status',
                                         max_length=4000 if key == 'options' else 200,
                                         style=discord.TextStyle.paragraph if key == 'options' else discord.TextStyle.short)
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction):
        if not await self.panel.interaction_check(interaction):
            return
        if self.panel.is_finished():
            await reply(interaction, 'Panel wygasł. Otwórz /praca ponownie.')
            return
        config = load_config()
        values = {key: field.value.strip() for key, field in self.inputs.items()}
        try:
            if self.kind == 'filters':
                if values['offer_status'] not in ('active', 'expired'):
                    raise ValueError('Status musi mieć wartość active lub expired.')
                for key, value in values.items():
                    config['filters'][key] = value
            elif self.kind == 'schedule':
                interval = int(values['interval_minutes'])
                if not MIN_INTERVAL_MINUTES <= interval <= 1440:
                    raise ValueError(f'Podaj interwał od {MIN_INTERVAL_MINUTES} do 1440 minut.')
                config['interval_minutes'] = interval
            else:
                provider = {'id': self.original['id'] if self.original else uuid.uuid4().hex,
                            'name': values['name'], 'type': values['type'], 'url': values['url'],
                            'enabled': self.original.get('enabled', True) if self.original else True,
                            'options': json.loads(values['options'])}
                if not provider['name'] or len(provider['name']) > 100:
                    raise ValueError('Nazwa musi mieć od 1 do 100 znaków.')
                validate_provider(provider)
                if self.original:
                    current = next((p for p in config['providers'] if p['id'] == self.original['id']), None)
                    if current != self.original:
                        raise ValueError('API zostało zmienione. Odśwież panel i otwórz edycję ponownie.')
                    config['providers'][config['providers'].index(current)] = provider
                else:
                    if len(config['providers']) >= 25:
                        raise ValueError('Można skonfigurować maksymalnie 25 API.')
                    config['providers'].append(provider)
                self.panel.selected = provider['id']
        except (ValueError, TypeError) as exc:
            # JSON errors contain no submitted credentials in the message.
            await reply(interaction, str(exc) if not isinstance(exc, json.JSONDecodeError) else 'Nieprawidłowy JSON opcji API.')
            return
        save_config(config)
        await self.panel.refresh(interaction)

    async def on_error(self, interaction, error):
        log.error('Job settings form failed: %s', type(error).__name__)
        await reply(interaction, 'Nie udało się zapisać ustawień. Spróbuj ponownie.')


class JobsPanel(discord.ui.LayoutView):
    def __init__(self, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.selected = None
        self.confirm_delete = False
        self.message = None
        self.render()

    def render(self):
        config = load_config()
        providers = config['providers']
        selected = next((p for p in providers if p['id'] == self.selected), None)
        self.clear_items()
        container = discord.ui.Container(accent_color=0x5865F2)
        container.add_item(discord.ui.TextDisplay('# 💼 Praca — konfiguracja\n'
            + ('🟢 Pobieranie włączone' if config.get('enabled', True) else '⏸️ Pobieranie wstrzymane')
            + f"\nKanał: <#{config['discord_channel_id']}> · co {config['interval_minutes']} min"))
        container.add_item(discord.ui.TextDisplay(discord.utils.escape_mentions(filters_summary(config['filters']))))
        container.add_item(discord.ui.ActionRow(
            PanelButton('filters', 'Edytuj filtry', discord.ButtonStyle.primary),
            PanelButton('remote', 'Zdalne: TAK' if config['filters'].get('include_remote') else 'Zdalne: NIE'),
            PanelButton('schedule', 'Interwał'), PanelButton('pause', 'Wstrzymaj' if config.get('enabled', True) else 'Wznów')))
        container.add_item(discord.ui.ActionRow(PanelSelect('levels', [
            discord.SelectOption(label=LEVEL_LABELS[level], value=level,
                                 default=level in normalize_allowed_levels(config['filters'].get('allowed_levels')))
            for level in ALL_LEVELS], placeholder='Dozwolone poziomy stanowisk', min_values=1, max_values=4)))
        container.add_item(discord.ui.TextDisplay('-# Oferty bez rozpoznanego poziomu są dozwolone.'))
        container.add_item(discord.ui.ActionRow(ChannelSelect()))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f'### API ({len(providers)})\nWybierz API, aby je skonfigurować. Nagłówki i klucze są ukryte.'))
        if providers:
            container.add_item(discord.ui.ActionRow(PanelSelect('api', [discord.SelectOption(
                label=p['name'][:100], value=p['id'], default=p['id'] == self.selected,
                description=f"{p['type']} · {'włączone' if p.get('enabled', True) else 'wyłączone'}") for p in providers], placeholder='Wybierz API')))
        container.add_item(discord.ui.ActionRow(
            PanelButton('add', 'Dodaj API', discord.ButtonStyle.success, len(providers) >= 25),
            PanelButton('edit', 'Konfiguruj API', disabled=selected is None),
            PanelButton('toggle', 'Włącz / wyłącz API', disabled=selected is None),
            PanelButton('delete', 'Usuń API', discord.ButtonStyle.danger, selected is None)))
        if self.confirm_delete and selected:
            container.add_item(discord.ui.TextDisplay('Potwierdź usunięcie wybranego API z konfiguracji.'))
            container.add_item(discord.ui.ActionRow(PanelButton('confirm_delete', 'Potwierdź usunięcie', discord.ButtonStyle.danger), PanelButton('cancel', 'Anuluj')))
        container.add_item(discord.ui.ActionRow(PanelButton('reset', 'Przywróć domyślne filtry'), PanelButton('refresh', 'Odśwież')))
        self.footer = discord.ui.TextDisplay('-# Zmiany zapisują się od razu. Pobieranie uwzględni je w kolejnym cyklu. Panel wygasa po 10 minutach.')
        container.add_item(self.footer)
        self.add_item(container)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id and has_high_tier_guard(interaction.user):
            return True
        await reply(interaction, 'Panel wymaga roli High Tier Guard. Otwórz własny przez /praca.')
        return False

    async def refresh(self, interaction):
        self.render()
        await interaction.response.edit_message(view=self, allowed_mentions=MENTIONS)

    async def action(self, interaction, action):
        config = load_config()
        selected = next((p for p in config['providers'] if p['id'] == self.selected), None)
        if action in ('filters', 'schedule', 'add', 'edit'):
            if action == 'edit' and selected is None:
                await reply(interaction, 'API zostało usunięte. Odśwież panel.')
                return
            await interaction.response.send_modal(SettingsModal(self, 'api' if action in ('add', 'edit') else action,
                                                                selected if action == 'edit' else None))
            return
        self.confirm_delete = action == 'delete' and selected is not None
        if action == 'remote':
            config['filters']['include_remote'] = not config['filters'].get('include_remote')
        elif action == 'pause':
            config['enabled'] = not config.get('enabled', True)
        elif action == 'toggle' and selected:
            selected['enabled'] = not selected.get('enabled', True)
        elif action == 'confirm_delete' and selected:
            config['providers'].remove(selected)
            self.selected = None
        elif action == 'reset':
            config['filters'] = copy.deepcopy(DEFAULT_CONFIG['filters'])
        if action in ('remote', 'pause', 'toggle', 'confirm_delete', 'reset'):
            save_config(config)
        await self.refresh(interaction)

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select, discord.ui.ChannelSelect)):
                item.disabled = True
        self.footer.content = '-# Panel wygasł. Otwórz /praca ponownie.'
        if self.message:
            try:
                await self.message.edit(view=self, allowed_mentions=MENTIONS)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction, error, item):
        log.error('Jobs panel failed: %s', type(error).__name__)
        await reply(interaction, 'Nie udało się zaktualizować panelu. Spróbuj ponownie.')


def register_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id=None):
    @tree.command(name='praca', description='Panel konfiguracji ofert pracy i źródeł API',
                  guild=discord.Object(id=guild_id or GUILD_ID))
    @app_commands.guild_only()
    async def praca(interaction: discord.Interaction):
        if not has_high_tier_guard(interaction.user):
            await reply(interaction, 'Nie masz wystarczających uprawnień. Wymagana rola High Tier Guard.')
            return
        panel = JobsPanel(interaction.user.id)
        await interaction.response.send_message(view=panel, ephemeral=True, allowed_mentions=MENTIONS)
        panel.message = await interaction.original_response()
