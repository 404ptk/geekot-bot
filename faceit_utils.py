import requests
import json
import discord
from discord import app_commands
from discord.ext import tasks
from datetime import datetime
import os
from startup_logger import record_startup_step

GUILD_ID = 551503797067710504

def load_token(filename, startup_label=None):
    try:
        with open(filename, 'r') as file:
            token = file.read().strip()
            if startup_label:
                record_startup_step(startup_label, True, filename)
            return token
    except FileNotFoundError:
        if startup_label:
            record_startup_step(startup_label, False, f"{filename} not found")
        else:
            print(f"File not found: {filename}. Make sure the file exists.")
        return None
    except Exception as e:
        if startup_label:
            record_startup_step(startup_label, False, f"{filename}: {e}")
        else:
            print(f"Error loading token from {filename}: {e}")
        return None

FACEIT_API_KEY = load_token('txt/faceit_api.txt', startup_label="Faceit API token")

# Lista pseudonimów graczy do rankingu Discorda
player_nicknames = ['utopiasz', 'radzioswir', 'PhesterM9', '-Masny-', '-mateuko', 'Kvzia', 'Kajetov', 'MlodyHubii']

FACEIT_RANKING_FILE = "txt/faceit_ranking.txt"
FACEIT_DAILY_STATS_FILE = "txt/faceit_daily_stats.json"
FACEIT_MATCHES_STATS_FILE = "txt/faceit_matches_stats.json"
CLIENT_REF = None  # Reference to the Discord client for background tasks


def get_faceit_player_data(nickname):
    url = f'https://open.faceit.com/data/v4/players?nickname={nickname}'
    headers = {'Authorization': f'Bearer {FACEIT_API_KEY}'}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        return None

def get_faceit_player_matches(player_id, limit=5, offset=0):
    game_id = "cs2"
    url = f"https://open.faceit.com/data/v4/players/{player_id}/games/{game_id}/stats?limit={limit}&offset={offset}"
    headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get("items", [])
    print("Błąd połączenia z Faceit API:", response.status_code)
    return None

def save_faceit_ranking(player_stats):
    with open(FACEIT_RANKING_FILE, "w") as file:
        json.dump(player_stats, file)

def load_faceit_ranking():
    try:
        with open(FACEIT_RANKING_FILE, "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def load_match_cache():
    """Load all cached match details."""
    if os.path.exists(FACEIT_MATCHES_STATS_FILE):
        try:
            with open(FACEIT_MATCHES_STATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_match_cache(cache_dict):
    """Save match cache to file."""
    try:
        with open(FACEIT_MATCHES_STATS_FILE, "w") as f:
            json.dump(cache_dict, f, indent=2)
    except Exception:
        pass

def _get_match_from_cache(match_id):
    """Check if match details exist in cache."""
    cache = load_match_cache()
    return cache.get(match_id)

def _save_match_to_cache(match_id, match_data):
    """Save match details to cache."""
    cache = load_match_cache()
    cache[match_id] = match_data
    save_match_cache(cache)

def get_faceit_match_details(match_id):
    # Check cache first
    cached = _get_match_from_cache(match_id)
    if cached:
        return cached
    
    url = f"https://open.faceit.com/data/v4/matches/{match_id}/stats"
    headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
    except requests.exceptions.Timeout:
        return None
    except requests.exceptions.RequestException:
        return None
    if response.status_code != 200:
        return None
    match_data = response.json()
    teams = {}
    for team in match_data["rounds"][0]["teams"]:
        team_name = team["team_id"]
        teams[team_name] = {"players": []}
        for player in team["players"]:
            teams[team_name]["players"].append({
                "nickname": player["nickname"],
                "kills": int(player["player_stats"]["Kills"]),
                "deaths": int(player["player_stats"]["Deaths"]),
                "assists": int(player["player_stats"]["Assists"]),
                "headshots": int(player["player_stats"]["Headshots %"]),
                "adr": player["player_stats"].get("ADR", "0"),
                "multikills": {
                    "2k": int(player["player_stats"].get("Double Kills", 0)),
                    "3k": int(player["player_stats"].get("Triple Kills", 0)),
                    "4k": int(player["player_stats"].get("Quadro Kills", 0)),
                    "5k": int(player["player_stats"].get("Penta Kills", 0))
                },
                "entry": {
                    "count": int(player["player_stats"].get("Entry Count", 0)),
                    "wins": int(player["player_stats"].get("Entry Wins", 0))
                },
                "clutch": {
                    "count": int(player["player_stats"].get("1v1Count", 0)) + int(player["player_stats"].get("1v2Count", 0)),
                    "wins": int(player["player_stats"].get("1v1Wins", 0)) + int(player["player_stats"].get("1v2Wins", 0))
                },
                "flash": {
                    "count": int(player["player_stats"].get("Flash Count", 0)),
                    "successes": int(player["player_stats"].get("Flash Successes", 0))
                },
                "utility_dmg": int(player["player_stats"].get("Utility Damage", 0)),
                "kr_ratio": player["player_stats"].get("K/R Ratio", "0"),
                "mvps": int(player["player_stats"].get("MVPs", 0)),
            })
    # Determine final score (e.g., 13:11)
    score = None
    try:
        round_stats = match_data.get("rounds", [{}])[0].get("round_stats", {})
        score_raw = round_stats.get("Score")
        if score_raw:
            score = score_raw.replace(" ", "").replace("/", ":")
        else:
            # Fallback: read from team_stats if available
            teams_list = match_data.get("rounds", [{}])[0].get("teams", [])
            if len(teams_list) == 2:
                t1 = teams_list[0].get("team_stats", {})
                t2 = teams_list[1].get("team_stats", {})
                s1 = t1.get("Final Score") or t1.get("Score") or t1.get("Team Score")
                s2 = t2.get("Final Score") or t2.get("Score") or t2.get("Team Score")
                if s1 and s2:
                    score = f"{s1}:{s2}"
    except Exception:
        pass
    result = {
        "map": match_data["rounds"][0]["round_stats"]["Map"],
        "teams": teams,
        "score": score,
    }
    # Save to cache
    _save_match_to_cache(match_id, result)
    return result

def get_faceit_match_roster(match_id):
    """Fetches match roster with level for all players (1 API call).
    ELO is fetched only for players in player_nicknames to minimize API calls."""
    url = f"https://open.faceit.com/data/v4/matches/{match_id}"
    headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return {}
    data = response.json()
    roster = {}
    for team_key in ["faction1", "faction2"]:
        team = data.get("teams", {}).get(team_key, {})
        for player in team.get("roster", []):
            nick = player.get("nickname", "")
            level = player.get("game_skill_level", 0)
            roster[nick] = {
                "elo": "",
                "level": level,
                "country": "",
            }
    # Fetch ELO and country for all players (same /players?nickname request)
    for nick in roster:
        p_data = get_faceit_player_data(nick)
        if p_data:
            elo = p_data.get('games', {}).get('cs2', {}).get('faceit_elo', '')
            roster[nick]["elo"] = str(elo)
            roster[nick]["country"] = p_data.get("country", "")
    return roster

def reset_faceit_ranking():
    if os.path.exists(FACEIT_RANKING_FILE):
        os.remove(FACEIT_RANKING_FILE)

def load_daily_stats():
    if os.path.exists(FACEIT_DAILY_STATS_FILE):
        try:
            with open(FACEIT_DAILY_STATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

# ----------------- SCHEDULED TASKS -----------------

@tasks.loop(minutes=10)
async def track_daily_elo():
    from faceit.tygodniowka import run_weekly_summary_if_due

    # Only run checks if client is ready
    if not CLIENT_REF or not CLIENT_REF.is_ready():
        return

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    await run_weekly_summary_if_due(CLIENT_REF, today=now)
                 
    # --- DAILY STATS (for comparison) ---
    daily_stats = load_daily_stats()
    if daily_stats.get("date") != today_str:
        # It's a new day, save current ELOs
        new_daily = {}
        for nick in player_nicknames:
            p_data = get_faceit_player_data(nick)
            if p_data:
                elo = p_data.get('games', {}).get('cs2', {}).get('faceit_elo')
                if isinstance(elo, int):
                    new_daily[nick] = elo
        
        daily_stats = {
            "date": today_str,
            "stats": new_daily
        }
        with open(FACEIT_DAILY_STATS_FILE, "w") as f:
            json.dump(daily_stats, f)


# ----------------- SLASH COMMANDS -----------------

async def setup_faceit_commands(client: discord.Client, tree: app_commands.CommandTree, guild_id: int = None):
    global CLIENT_REF
    CLIENT_REF = client
    guild = discord.Object(id=guild_id) if guild_id else discord.Object(id=GUILD_ID)

    # Autocomplete callback for Faceit nickname
    async def faceit_nick_autocomplete(interaction: discord.Interaction, current: str):
        query = (current or "").lower()
        options = [n for n in player_nicknames if query in n.lower()]
        return [app_commands.Choice(name=n, value=n) for n in options[:25]]

    from faceit.faceit import register_faceit_command

    register_faceit_command(
        tree=tree,
        guild=guild,
        faceit_nick_autocomplete=faceit_nick_autocomplete,
    )

    from faceit.last import register_last_command

    register_last_command(
        tree=tree,
        guild=guild,
        faceit_nick_autocomplete=faceit_nick_autocomplete,
    )

    from faceit.discordfaceit import register_discordfaceit_command

    register_discordfaceit_command(
        tree=tree,
        guild=guild,
    )

    from faceit.compare import register_compare_command

    register_compare_command(
        tree=tree,
        guild=guild,
        faceit_nick_autocomplete=faceit_nick_autocomplete,
    )

    from faceit.scout import register_scout_command

    register_scout_command(tree=tree, guild=guild)

    @tree.command(
        name="resetfaceitranking",
        description="Resetuje ranking Faceit (czyści plik rankingowy)",
        guild=guild
    )
    async def resetfaceitranking(interaction: discord.Interaction):
        if interaction.user.id != 443406275716579348:
            await interaction.response.send_message(
                "❌ Nie masz uprawnień do tej komendy.", ephemeral=True
            )
            return

        reset_faceit_ranking()
        await interaction.response.send_message("✅ Ranking Faceit został zresetowany (plik faceit_ranking.txt usunięty).", ephemeral=True)

    if not track_daily_elo.is_running():
        track_daily_elo.start()

    from faceit.live import start_faceit_live_tracking

    await start_faceit_live_tracking(client)

    from faceit.sieroty import register_sieroty_commands

    register_sieroty_commands(
        tree=tree,
        guild=guild,
        faceit_nick_autocomplete=faceit_nick_autocomplete,
    )
