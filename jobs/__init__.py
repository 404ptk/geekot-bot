"""Job watcher package with a backwards compatible setup entry point."""


async def setup_jobs_watch(client, tree, guild_id=None):
    from jobs.setup import setup_jobs_watch as setup

    await setup(client, tree, guild_id=guild_id)
