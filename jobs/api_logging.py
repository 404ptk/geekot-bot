"""Report API state changes and remind about persistent failures hourly."""

import threading
import time

_states = {}
_lock = threading.Lock()
REMINDER_SECONDS = 3600


def error_summary(exc):
    response = getattr(exc, "response", None)
    if response is not None:
        return f"HTTP {response.status_code} {response.reason}"
    return f"{type(exc).__name__}: {exc}"


def report_api_state(key, state, message, *, failed=False, report_initial=True):
    now = time.monotonic()
    with _lock:
        previous = _states.get(key)
        should_report = (
            previous is None
            or previous[0] != state
            or (failed and now - previous[1] >= REMINDER_SECONDS)
        )
        if should_report:
            _states[key] = (state, now)
    if should_report and (report_initial or previous is not None):
        print(message)


def report_provider_error(provider, exc):
    reason = error_summary(exc)
    report_api_state(
        provider, reason, f"[Jobs][{provider}] API error: {reason}", failed=True
    )


def report_provider_success(provider):
    report_api_state(
        provider, None, f"[Jobs][{provider}] API dostępne ponownie.", report_initial=False
    )
