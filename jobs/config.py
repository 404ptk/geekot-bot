import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from jobs.constants import DEFAULT_CONFIG

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TXT_DIR = PROJECT_ROOT / "txt"
CONFIG_FILE = TXT_DIR / "jobs_watch.json"
STATE_FILE = TXT_DIR / "jobs_state.json"

_config: Dict[str, Any] = {}


def load_json(path: Path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[Jobs] Failed to read {path}: {e}")
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_config() -> Dict[str, Any]:
    global _config
    stored = load_json(CONFIG_FILE, None)
    if not stored:
        _config = json.loads(json.dumps(DEFAULT_CONFIG))
        save_json(CONFIG_FILE, _config)
        return _config

    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in stored.items() if k != "filters"})
    merged["filters"] = {**DEFAULT_CONFIG["filters"], **stored.get("filters", {})}
    _config = merged
    return _config


def save_config(config: Dict[str, Any]):
    global _config
    save_json(CONFIG_FILE, config)
    _config = config


def filter_signature(filters: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(filters, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
