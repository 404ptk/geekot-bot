from bot_health import monitor, failure as health_failure
import asyncio
from datetime import datetime, timezone

import discord

from jobs.api_logging import report_api_state
from jobs.api_status import build_status_embed, check_all_services
from jobs.config import load_config
from jobs.constants import API_STATUS_INTERVAL_MINUTES, API_STATUS_MESSAGE_ID
from jobs.discord_utils import get_discord_channel

_update_lock = asyncio.Lock()
_running_tasks = []


@monitor.task('jobs_api', 'Stan API ofert', API_STATUS_INTERVAL_MINUTES * 60,
              disabled=lambda: 'Brak włączonych API.' if not any(p.get('enabled', True) for p in load_config()['providers']) else None)
async def update_api_status_message(client: discord.Client):
    async with _update_lock:
        config = load_config()
        channel = await get_discord_channel(client, int(config["discord_channel_id"]))
        if not channel:
            health_failure("Nie można odczytać kanału statusu API.")
            print("[Jobs] Cannot update API status message - channel not found")
            return

        results, failures = await asyncio.to_thread(check_all_services)
        if failures:
            health_failure("Co najmniej jedno API ofert nie odpowiedziało poprawnie.")
        checked_at = datetime.now(timezone.utc)
        embed = build_status_embed(results, checked_at)

        try:
            message = await channel.fetch_message(API_STATUS_MESSAGE_ID)
            await message.edit(embed=embed)
            successful = sum(1 for is_available in results.values() if is_available)
            total = len(results)
            percentage = (successful / total * 100) if total else 0
            summary = f"[Jobs] API status message updated. Sukces: {successful}/{total} ({percentage:.1f}%)."
            if failures:
                failed_services = ", ".join(
                    f"{service}: {reason}" for service, reason in failures.items()
                )
                summary += f" Niedziałające: {failed_services}."
            else:
                summary += " Wszystkie usługi działają."
            report_api_state(
                "api_status", (tuple(sorted(results.items())), tuple(sorted(failures.items()))),
                summary, failed=bool(failures),
            )
        except discord.NotFound as exc:
            health_failure(exc)
            print(f"[Jobs] API status message {API_STATUS_MESSAGE_ID} not found.")
        except Exception as exc:
            health_failure(exc)
            print(f"[Jobs] Failed to update API status message: {exc}")


async def status_worker(client: discord.Client):
    monitor.modules["jobs_api"].next_due = datetime.now(timezone.utc).timestamp() + 15
    await asyncio.sleep(15)
    while True:
        await update_api_status_message(client)
        monitor.modules["jobs_api"].next_due = datetime.now(timezone.utc).timestamp() + API_STATUS_INTERVAL_MINUTES * 60
        await asyncio.sleep(API_STATUS_INTERVAL_MINUTES * 60)


def start_api_status_worker(client: discord.Client):
    monitor.modules["jobs_api"].retry = lambda: update_api_status_message(client)
    task = asyncio.create_task(status_worker(client))
    _running_tasks.append(task)
    print(
        f"[Jobs] API status worker started "
        f"(message {API_STATUS_MESSAGE_ID}, every {API_STATUS_INTERVAL_MINUTES} min)"
    )
