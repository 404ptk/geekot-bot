"""Local health measurements for scheduled work; no raw exception text is stored."""
import asyncio
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import inspect
import json
import math
from pathlib import Path
import time
import traceback

import discord

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / 'txt/bot_health_settings.json'
HISTORY_FILE = ROOT / 'txt/bot_health_state.json'
PROCESS_STARTED = time.monotonic()
_current = ContextVar('health_run', default=None)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def load_settings():
    return {'enabled': False, 'channel_id': None, 'threshold': 3,
            **read_json(SETTINGS_FILE, {})}


def update_settings(**changes):
    settings = load_settings()
    settings.update(changes)
    if not 1 <= int(settings['threshold']) <= 20:
        raise ValueError('Próg musi wynosić od 1 do 20 nieudanych prób.')
    write_json(SETTINGS_FILE, settings)


def safe_error(error):
    """Allowlist metadata: never include messages, source lines, locals or URLs."""
    if isinstance(error, str):
        # Only callers with a fixed, developer-written explanation use strings.
        return error[:200]
    name = type(error).__name__
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None) or getattr(error, 'status', None)
    if isinstance(status, int):
        name += f' · HTTP {status}'
    frames = traceback.extract_tb(error.__traceback__) if error.__traceback__ else []
    locations = [f'{Path(f.filename).name}:{f.lineno} ({f.name})' for f in frames[-5:]]
    return name + ('\n' + '\n'.join(locations) if locations else '')


def failure(error):
    run = _current.get()
    if run is not None and len(run['errors']) < 5:
        run['errors'].append(safe_error(error))


def skip(reason):
    run = _current.get()
    if run is not None:
        run['skip'] = reason


@dataclass
class Module:
    key: str
    label: str
    interval: object
    disabled: object = None
    retry: object = None
    scheduled: object = None
    next_due: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    registered: float = field(default_factory=time.time)
    last_check: float | None = None
    last_attempt: float | None = None
    last_success: float | None = None
    started: float | None = None
    duration: float | None = None
    failures: int = 0
    error: str = ''
    skip_reason: str = ''
    history: deque = field(default_factory=lambda: deque(maxlen=20))
    alerted: bool = False
    alert_kind: str = ''
    last_manual: float = -math.inf
    pending: bool = False

    def seconds(self):
        return max(1, float(self.interval() if callable(self.interval) else self.interval))

    def next_check(self):
        scheduled = self.scheduled() if self.scheduled else None
        if scheduled is not None:
            return scheduled.timestamp()
        return self.next_due or (self.last_check or self.registered) + self.seconds()

    def disabled_reason(self):
        try:
            return self.disabled() if self.disabled else None
        except Exception:
            return None  # A broken config is a failure, not an intentional pause.

    def status(self, now=None):
        now = time.time() if now is None else now
        if self.disabled_reason():
            return 'disabled'
        if self.started is not None:
            return 'stale' if now - self.started > max(300, self.seconds()) else 'running'
        if now > self.next_check() + max(60, self.seconds() * .5):
            return 'stale'
        if self.failures:
            return 'error' if self.failures >= load_settings()['threshold'] else 'warning'
        if self.last_check is None or self.skip_reason:
            return 'waiting'
        return 'ok'


class HealthMonitor:
    def __init__(self):
        self.modules = {}
        self.client = None
        self.worker = None
        self.manual_tasks = set()
        self.storage_error = False
        self.alert_error = False
        self.saved = read_json(HISTORY_FILE, {})

    def register(self, key, label, interval, disabled=None):
        state = Module(key, label, interval, disabled)
        old = self.saved.get(key, {})
        state.last_success = old.get('last_success')
        state.alerted = bool(old.get('alerted'))
        state.alert_kind = old.get('alert_kind', '')
        state.failures = old.get('failures', 0)
        state.error = old.get('error', '')
        state.last_attempt = old.get('last_attempt')
        state.duration = old.get('duration')
        state.history.extend(old.get('history', [])[-20:])
        self.modules[key] = state
        return state

    def persist(self):
        try:
            write_json(HISTORY_FILE, {key: {'last_success': s.last_success,
                'alerted': s.alerted, 'alert_kind': s.alert_kind, 'failures': s.failures,
                'error': s.error, 'last_attempt': s.last_attempt, 'duration': s.duration, 'history': list(s.history)} for key, s in self.modules.items()})
            self.storage_error = False
        except OSError:
            self.storage_error = True

    def task(self, key, label, interval, disabled=None):
        def decorate(func):
            state = self.register(key, label, interval, disabled)
            @wraps(func)
            async def wrapped(*args, **kwargs):
                return await self.run(key, lambda: func(*args, **kwargs))
            if not any(p.default is inspect.Parameter.empty and p.kind in (
                    p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                    for p in inspect.signature(func).parameters.values()):
                state.retry = wrapped
            return wrapped
        return decorate

    async def run(self, key, callback):
        state = self.modules[key]
        if state.lock.locked():
            return False
        async with state.lock:
            disabled = state.disabled_reason()
            if disabled:
                state.last_check = time.time()
                state.skip_reason = disabled
                return False
            start = time.time()
            state.started = start
            run = {'errors': [], 'skip': ''}
            token = _current.set(run)
            cancelled = False
            result = None
            try:
                result = await callback()
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as exc:
                failure(exc)
            finally:
                _current.reset(token)
                state.started = None
                if not cancelled:
                    state.last_check = time.time()
                    state.skip_reason = run['skip']
                    if run['errors'] or not run['skip']:
                        state.last_attempt = start
                        state.duration = max(0, state.last_check - start)
                        state.error = '\n'.join(run['errors'])[:1800]
                        state.failures = state.failures + 1 if run['errors'] else 0
                        if not run['errors']:
                            state.last_success = state.last_check
                        state.history.append({'at': start, 'ok': not bool(run['errors']),
                                              'duration': round(state.duration, 2), 'error': state.error})
                        self.persist()
            return result

    def launch_retry(self, key):
        state = self.modules[key]
        if not state.retry:
            return 'Moduł nie jest jeszcze gotowy do ręcznego uruchomienia.'
        if state.disabled_reason():
            return f'Moduł niedostępny: {state.disabled_reason()}.'
        if state.lock.locked() or state.pending:
            return 'To zadanie już trwa. Poczekaj na zakończenie.'
        if time.monotonic() - state.last_manual < 60:
            return 'Ponawianie jest dostępne raz na minutę dla danego modułu.'
        state.pending = True
        state.last_manual = time.monotonic()
        async def execute():
            try:
                await state.retry()
            finally:
                state.pending = False
        task = asyncio.create_task(execute())
        self.manual_tasks.add(task)
        task.add_done_callback(self.manual_tasks.discard)
        return 'Uruchomiono sprawdzenie. Kliknij Odśwież, aby zobaczyć wynik. Harmonogram i zabezpieczenia przed ponowną publikacją nadal obowiązują.'

    async def alerts_once(self):
        settings = load_settings()
        if not settings['enabled'] or not settings['channel_id'] or not self.client:
            return
        sent = 0
        for state in list(self.modules.values()):
            status = state.status()
            incident = status == 'stale' or (status == 'error' and state.failures >= settings['threshold'])
            recovered = (state.alerted and state.last_success is not None
                         and state.last_check is not None and state.failures == 0
                         and state.last_success >= state.registered and status == 'ok')
            if state.alerted and state.alert_kind == 'stale':
                recovered = state.last_check is not None and state.failures == 0 and status in ('ok', 'waiting')
            if not ((incident and not state.alerted) or recovered):
                continue
            channel = self.client.get_channel(int(settings['channel_id']))
            if channel is None:
                channel = await self.client.fetch_channel(int(settings['channel_id']))
            detail = 'Zadanie nie zakończyło się w oczekiwanym czasie.' if status == 'stale' else state.error
            text = (f'🔴 **{state.label}** — problem\n{detail}\nKolejne nieudane próby: {state.failures}'
                    if incident else f'🟢 **{state.label}** — ponownie działa. Zadanie zgłasza prawidłowy stan.')
            await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
            state.alerted = incident
            state.alert_kind = status if incident else ""
            self.persist()
            sent += 1
            if sent >= 4:
                break

    def start(self, client):
        self.client = client
        if self.worker and not self.worker.done():
            return
        async def watch():
            await client.wait_until_ready()
            while not client.is_closed():
                try:
                    await self.alerts_once()
                    self.alert_error = False
                except Exception:
                    self.alert_error = True
                await asyncio.sleep(30)
        self.worker = asyncio.create_task(watch())


def process_stats():
    rss = None
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                rss = int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    uptime = int(time.monotonic() - PROCESS_STARTED)
    try:
        # Linux process start time rather than the time this module was imported.
        import os
        started = int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]) / os.sysconf('SC_CLK_TCK')
        uptime = int(float(Path('/proc/uptime').read_text().split()[0]) - started)
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    return uptime, rss


monitor = HealthMonitor()
