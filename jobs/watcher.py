from bot_health import monitor
import asyncio
from datetime import datetime, timezone

import discord

from jobs.constants import DEFAULT_CONFIG, INIT_SEED_PAGES, MIN_INTERVAL_MINUTES
from jobs.config import filter_signature, load_config
from jobs.poster import post_offers
from jobs.providers import fetch_matching_offers
from jobs.state import load_state, save_state, trim_seen_uuids

_running_tasks = []


@monitor.task('jobs', 'Oferty pracy', lambda: max(MIN_INTERVAL_MINUTES, int(load_config().get('interval_minutes', 30))) * 60,
              disabled=lambda: ('Pobieranie ofert wyłączone.' if not load_config().get('enabled', True)
                  else 'Brak włączonych API.' if not any(p.get('enabled', True) for p in load_config()['providers']) else None))
async def check_and_post(client: discord.Client):
    from startup_guard import allow_background_api

    if not allow_background_api("watcher ofert pracy"):
        return

    config = load_config()
    if not config.get("enabled", True):
        return
    filters = config.get("filters", DEFAULT_CONFIG["filters"])
    channel_id = int(config["discord_channel_id"])
    signature = filter_signature({"filters": filters, "providers": config["providers"]})

    state = load_state()
    seen = set(state.get("seen_uuids", []))
    initialized = state.get("initialized", False) and state.get("filter_signature") == signature

    if not initialized:
        seed_offers = await asyncio.to_thread(fetch_matching_offers, filters, INIT_SEED_PAGES, config["providers"])
        seed_offers = [offer for offer in seed_offers if offer.get("offer_uuid") not in seen]
        for offer in seed_offers:
            offer_uuid = offer.get("offer_uuid")
            if offer_uuid:
                seen.add(offer_uuid)

        if seed_offers:
            seed_offers.sort(key=lambda item: item.get("offer_published_at") or "", reverse=True)
            await post_offers(client, seed_offers, channel_id)

        state = {
            "initialized": True,
            "filter_signature": signature,
            "seen_uuids": trim_seen_uuids(seen),
            "last_check": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
        print(f"[Jobs] Initialized watcher with {len(seed_offers)} offers (posted on first run).")
        return

    page_offers = await asyncio.to_thread(fetch_matching_offers, filters, 1, config["providers"])
    new_offers = []
    for offer in page_offers:
        offer_uuid = offer.get("offer_uuid")
        if not offer_uuid or offer_uuid in seen:
            continue
        seen.add(offer_uuid)
        new_offers.append(offer)

    if new_offers:
        new_offers.sort(key=lambda item: item.get("offer_published_at") or "", reverse=True)
        await post_offers(client, new_offers, channel_id)
        print(f"[Jobs] Posted {len(new_offers)} new offer(s).")

    state["seen_uuids"] = trim_seen_uuids(seen)
    state["filter_signature"] = signature
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


async def watcher_worker(client: discord.Client):
    config = load_config()
    interval_minutes = max(MIN_INTERVAL_MINUTES, int(config.get("interval_minutes", 30)))
    monitor.modules["jobs"].next_due = datetime.now(timezone.utc).timestamp() + 10
    await asyncio.sleep(10)

    while True:
        try:
            await check_and_post(client)
        except Exception as exc:
            print(f"[Jobs] Watcher check failed: {type(exc).__name__}")
        config = load_config()
        interval_minutes = max(MIN_INTERVAL_MINUTES, int(config.get("interval_minutes", 30)))
        monitor.modules["jobs"].next_due = datetime.now(timezone.utc).timestamp() + interval_minutes * 60
        await asyncio.sleep(interval_minutes * 60)


def start_watcher(client: discord.Client):
    monitor.modules["jobs"].retry = lambda: check_and_post(client)
    config = load_config()
    task = asyncio.create_task(watcher_worker(client))
    _running_tasks.append(task)
    print(
        f"[Jobs] Watcher started -> channel {config.get('discord_channel_id')} "
        f"(every {config.get('interval_minutes', 30)} min)"
    )
