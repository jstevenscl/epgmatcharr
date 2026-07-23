import hashlib
import os
import json
import secrets
from pathlib import Path

DATA_DIR    = Path(os.environ.get("DATA_DIR", "/app/data"))
CONFIG_FILE = DATA_DIR / "config.json"
APP_PORT    = int(os.environ.get("APP_PORT", "8281"))

EPG_SETTINGS_DEFAULTS = {
    "epg_cache_ttl_hours":     1.0,
    "epg_window_hours_before": 0.5,
    "epg_window_hours_after":  3.0,
    "guide_window_hours":      2.0,
    "backfill_gn_id":          False,
    "backfill_tvg_id":         False,
    "enable_epg_guide":        True,
}


def _read_raw() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _write_raw(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def get_config() -> tuple[str, str]:
    url   = os.environ.get("DISPATCHARR_URL", "").rstrip("/")
    token = os.environ.get("DISPATCHARR_TOKEN", "")
    if url and token:
        return url, token
    data = _read_raw()
    return data.get("dispatcharr_url", "").rstrip("/"), data.get("dispatcharr_token", "")


def save_config(url: str, token: str) -> None:
    data = _read_raw()
    data.update({"dispatcharr_url": url.rstrip("/"), "dispatcharr_token": token})
    _write_raw(data)


def get_xmltv_url() -> str:
    return _read_raw().get("xmltv_url", "")


def save_xmltv_url(url: str) -> None:
    data = _read_raw()
    data["xmltv_url"] = url.strip()
    _write_raw(data)


def config_from_env() -> bool:
    return bool(os.environ.get("DISPATCHARR_URL") and os.environ.get("DISPATCHARR_TOKEN"))


def is_configured() -> bool:
    url, token = get_config()
    return bool(url and token)


def get_epg_settings() -> dict:
    data     = _read_raw()
    defaults = dict(EPG_SETTINGS_DEFAULTS)
    for key in defaults:
        if key not in data:
            continue
        if isinstance(defaults[key], bool):
            defaults[key] = bool(data[key])
        else:
            try:
                defaults[key] = float(data[key])
            except (TypeError, ValueError):
                pass
    return defaults


def save_epg_settings(
    ttl_hours: float,
    window_before: float,
    window_after: float,
    guide_window_hours: float = 2.0,
    backfill_gn_id: bool = False,
    backfill_tvg_id: bool = False,
    enable_epg_guide: bool = True,
) -> None:
    data = _read_raw()
    data.update({
        "epg_cache_ttl_hours":     max(0.25, float(ttl_hours)),
        "epg_window_hours_before": max(0.0,  float(window_before)),
        "epg_window_hours_after":  max(0.5,  float(window_after)),
        "guide_window_hours":      max(0.5,  float(guide_window_hours)),
        "backfill_gn_id":          bool(backfill_gn_id),
        "backfill_tvg_id":         bool(backfill_tvg_id),
        "enable_epg_guide":        bool(enable_epg_guide),
    })
    _write_raw(data)


# ── Emby integration ─────────────────────────────────────────────────────────

def emby_config_from_env() -> bool:
    return bool(os.environ.get("EMBY_URL") and os.environ.get("EMBY_API_KEY"))


def get_emby_config() -> dict:
    data = _read_raw()
    env_url     = os.environ.get("EMBY_URL", "").rstrip("/")
    env_api_key = os.environ.get("EMBY_API_KEY", "")
    if env_url and env_api_key:
        url, api_key = env_url, env_api_key
    else:
        url, api_key = data.get("emby_url", "").rstrip("/"), data.get("emby_api_key", "")
    return {
        "url":        url,
        "api_key":    api_key,
        "zip_codes":  data.get("emby_zip_codes", []),
        "country":    data.get("emby_country", "US"),
        "group_ids":  data.get("emby_group_ids", []),
    }


def save_emby_config(url: str, api_key: str, zip_codes: list[str], country: str = "US", group_ids: list[int] | None = None) -> None:
    data = _read_raw()
    data.update({
        "emby_url":        url.rstrip("/").strip(),
        "emby_api_key":    api_key.strip(),
        "emby_zip_codes":  [z.strip() for z in zip_codes if z.strip()],
        "emby_country":    (country or "US").strip().upper(),
        "emby_group_ids":  list(group_ids) if group_ids else [],
    })
    _write_raw(data)


def is_emby_configured() -> bool:
    cfg = get_emby_config()
    return bool(cfg["url"] and cfg["api_key"])


# ── Emby Sync exclusions ─────────────────────────────────────────────────────
# A channel can have a perfectly correct GN station ID (GN Matcher isn't wrong
# about it) and still be something the user simply never wants pushed to Emby --
# e.g. SiriusXM audio channels that happen to share a name with a real TV
# channel. Emby-Sync-specific; deliberately not tied to GN Matcher's own
# per-run group filter, which serves a much larger, non-Emby audience.

def get_emby_excluded_groups() -> list[int]:
    data = _read_raw()
    return list(data.get("emby_excluded_group_ids", []))


def save_emby_excluded_groups(group_ids: list[int]) -> None:
    data = _read_raw()
    data["emby_excluded_group_ids"] = sorted({int(g) for g in group_ids})
    _write_raw(data)


# ── GN Matcher group filter ───────────────────────────────────────────────────
# Remembers the last channel-group filter selected on the GN Matcher page, so
# a user with many groups (most of which don't use GN IDs) doesn't have to
# reselect the same few groups on every page load.

def get_gn_matcher_group_filter() -> list[int]:
    data = _read_raw()
    return list(data.get("gn_matcher_group_ids", []))


def save_gn_matcher_group_filter(group_ids: list[int]) -> None:
    data = _read_raw()
    data["gn_matcher_group_ids"] = sorted({int(g) for g in group_ids})
    _write_raw(data)


# ── Auth ──────────────────────────────────────────────────────────────────────

def has_credentials() -> bool:
    if os.environ.get("EPGMATCHARR_ADMIN_USER") and os.environ.get("EPGMATCHARR_ADMIN_PASSWORD"):
        return True
    data = _read_raw()
    return bool(data.get("auth_username") and data.get("auth_hash"))


def verify_credentials(username: str, password: str) -> bool:
    env_user = os.environ.get("EPGMATCHARR_ADMIN_USER", "")
    env_pass = os.environ.get("EPGMATCHARR_ADMIN_PASSWORD", "")
    if env_user and env_pass:
        return (
            secrets.compare_digest(username.encode(), env_user.encode()) and
            secrets.compare_digest(password.encode(), env_pass.encode())
        )
    data        = _read_raw()
    stored_user = data.get("auth_username", "")
    stored_salt = data.get("auth_salt", "")
    stored_hash = data.get("auth_hash", "")
    if not (stored_user and stored_salt and stored_hash):
        return False
    candidate = hashlib.sha256((stored_salt + password).encode()).hexdigest()
    return (
        secrets.compare_digest(username.encode(), stored_user.encode()) and
        secrets.compare_digest(candidate.encode(), stored_hash.encode())
    )


def set_credentials(username: str, password: str) -> None:
    salt   = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    data   = _read_raw()
    data.update({"auth_username": username, "auth_salt": salt, "auth_hash": hashed})
    _write_raw(data)
