GUILD_ID = 551503797067710504

ALL_LEVELS = ("internship", "junior", "mid", "senior")
DEFAULT_ALLOWED_LEVELS = ["internship", "junior", "mid"]
LEVEL_LABELS = {
    "internship": "Internship",
    "junior": "Junior",
    "mid": "Mid",
    "senior": "Senior",
}
LEVEL_PATTERNS = {
    "internship": (
        r"\binternship\b",
        r"\bintern\b",
        r"\bstaż\b",
        r"\bstaz\b",
    ),
    "junior": (
        r"\bjunior\b",
        r"\btrainee\b",
    ),
    "senior": (
        r"\bsenior\b",
        r"\bsr\.?\b",
        r"\bprincipal\b",
    ),
    "mid": (
        r"\bmid[\s-]?level\b",
        r"\bmid\b",
        r"\bregular\b",
    ),
}

LOCAL_FILTER_KEYS = ("location_city", "include_remote", "allowed_levels")
DISMISS_EMOJI = "\N{CROSS MARK}"

MAX_SEEN_UUIDS = 2000
INIT_SEED_PAGES = 3
MIN_INTERVAL_MINUTES = 15
BULK_POST_THRESHOLD = 10
BULK_POST_DELAY_SECONDS = 60
NORMAL_POST_DELAY_SECONDS = 1.5
API_STATUS_INTERVAL_MINUTES = 30
API_STATUS_MESSAGE_ID = 1519738959003914434

DEFAULT_PROVIDERS = [
    {"id": "isitfair", "name": "IsItFair", "type": "isitfair", "url": "https://isitfair.pl/api/v1/offers/search", "enabled": True, "options": {}},
    {"id": "olx", "name": "OLX Praca", "type": "olx", "url": "https://www.olx.pl/api/v1/offers/", "enabled": True, "options": {}},
]

DEFAULT_CONFIG = {
    "enabled": True,
    "providers": DEFAULT_PROVIDERS,
    "discord_channel_id": 1518628748881039452,
    "interval_minutes": 30,
    "filters": {
        "offer_status": "active",
        "location_city": "Rzeszów",
        "include_remote": True,
        "allowed_levels": list(DEFAULT_ALLOWED_LEVELS),
    },
}
