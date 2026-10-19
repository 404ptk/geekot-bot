from jobs.poster import register_dismiss_listener
from jobs.watcher import start_watcher
from jobs.api_status_worker import start_api_status_worker


async def setup_jobs_watch(client, tree, guild_id=None):
    register_dismiss_listener(client)
    start_watcher(client)
    start_api_status_worker(client)
