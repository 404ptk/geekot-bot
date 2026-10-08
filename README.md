# Geekot Bot

A Discord bot for a private community, with slash commands for CS2 stats, activity tracking, gaming coordination, and community tools. It runs as a single process and stores configuration and state in local JSON/TXT files.

## Tech and integrations

- Python 3.10+ and `discord.py` 2.x; slash commands and scheduled background tasks.
- `requests` and `aiohttp` for HTTP, Pillow for image processing.
- Faceit, Leetify and YouTube Data API. Some features also read public Steam/GitHub feeds and job listing sites.
- No in-house HTTP API or database. Local files under `txt/` hold secrets, settings, and runtime data.

## Setup

You need Python 3.10+ and a bot application from the [Discord Developer Portal](https://discord.com/developers/applications). Enable the **Server Members**, **Message Content**, and **Presence** Gateway Intents, then invite the bot with permissions for the commands and channels it uses.

```bash
git clone <repository-url>
cd geekot-bot
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
mkdir -p txt
```

Create these files in `txt/`. Each should contain only the secret value, without quotes:

| File | Used for |
| --- | --- |
| `discord_token.txt` | Required to start the bot |
| `faceit_api.txt` | Faceit commands |
| `leetify_api.txt` | Leetify commands |

Optional features may need additional files, including `youtube_api_key.txt`, `youtube_shorts.json`, `google_service_account.json`, and `drive_daily.json`. Some runtime data files are created automatically. Features that need missing optional credentials will not work.

```bash
python main.py
```

## Notes

- Relation users default to the existing eight nicknames. `/config relacje dodaj` adds a nickname with two required Polish forms: `celownik` (e.g. "wypowiedział kosę jarowi") and `narzednik` (e.g. "trzyma zgodę z jarem"). `/config relacje usun` offers nickname autocomplete and removes the user together with their relations and temporary agreements. The list is persisted in `txt/relation_users.json`, including removals of default users.
- There is no central configuration template; setup currently requires creating files in `txt/` manually.
- Runtime state is stored in JSON/TXT files, with no database or built-in backup mechanism.
