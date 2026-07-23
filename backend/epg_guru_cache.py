"""
epg.guru pre-parsed cache — avoids in-process parsing of the largest epg.guru
XMLTV sources (140-341MB compressed, 10-27M elements each), which was
observed to starve the GIL badly enough to freeze the whole app for minutes
at a time. A scheduled GitHub Action parses these in an isolated CI runner
instead and publishes one small, gzip-compressed SQLite file per market/tier
as a release; this module downloads and decompresses only the specific
file(s) matching whatever sources a user actually has configured — not the
full bundle — and serves now-playing queries out of them directly.

Exact URL matching only — never a fuzzy/substring match. The upstream
epg.guru files are unauthenticated and identical for every requester, so a
byte-for-byte URL match guarantees the tvg_id values in our cached copy are
identical to what the user's own direct fetch of that same URL would give.
Anything not an exact match falls through to the normal direct-fetch path.
"""

import gzip
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from config import DATA_DIR

logger = logging.getLogger(__name__)

_RELEASES_API = "https://api.github.com/repos/jstevenscl/epgmatcharr/releases?per_page=20"
_HTTP_HEADERS = {"User-Agent": "EPGmatcharr/1.0 (+https://github.com/jstevenscl/epgmatcharr)"}

_CACHE_DIR = DATA_DIR / "epg_guru_cache"

# Re-check GitHub for a newer release at most this often, per asset — the
# Action itself only publishes every 4h, so checking every warm cycle
# (~hourly) is already more than enough to pick up new releases promptly
# without hammering the API.
_RECHECK_INTERVAL_SECONDS = 3600

_MARKETS = ["Canada", "USFast", "UnitedStates", "UnitedStates-Locals", "FullGuide"]
_TIERS   = ["7daygracenote", "7dayiptv"]
# FullGuide is the all-countries-combined file; unlike the other 4 markets
# it's confirmed mirrored on both epg.guru and cdn.epg.guru, so both hosts
# are matched for every market — not just the ones a user is known to have
# configured — since either mirror could show up in a user's Dispatcharr
# source URL.
_HOSTS = ["epg.guru", "cdn.epg.guru"]


def _asset_name(market: str, tier: str) -> str:
    return f"epg_guru_cache_{market}_{tier}.sqlite.gz"


# url -> (market, tier). Deliberately an exact, exhaustive map — NOT a
# pattern/substring match. See module docstring. Covers both the .xml.gz
# and uncompressed .xml variant of each source (epg.guru serves both, and
# users have been observed configuring either one) and both known mirror
# hosts.
_URL_TO_MARKET_TIER: dict[str, tuple[str, str]] = {
    f"https://{host}/{tier}/{market}.xml{suffix}": (market, tier)
    for market in _MARKETS
    for tier in _TIERS
    for host in _HOSTS
    for suffix in ("", ".gz")
}

KNOWN_URLS: frozenset[str] = frozenset(_URL_TO_MARKET_TIER)

_last_check_at: dict[str, float] = {}
_download_locks: dict[str, "object"] = {}


def _lock_for(asset: str):
    import asyncio
    lock = _download_locks.get(asset)
    if lock is None:
        lock = asyncio.Lock()
        _download_locks[asset] = lock
    return lock


def is_known_url(url: str) -> bool:
    return url in KNOWN_URLS


def _local_path(asset: str) -> Path:
    return _CACHE_DIR / asset.removesuffix(".gz")


async def _maybe_refresh(market: str, tier: str) -> None:
    """Download+decompress the latest release asset for this one market/tier
    if we haven't checked recently. Never touches the other 7 assets.
    """
    global _last_check_at
    asset      = _asset_name(market, tier)
    local_path = _local_path(asset)
    now        = time.monotonic()
    last       = _last_check_at.get(asset, 0.0)
    if local_path.exists() and (now - last) < _RECHECK_INTERVAL_SECONDS:
        return

    async with _lock_for(asset):
        now  = time.monotonic()
        last = _last_check_at.get(asset, 0.0)
        if local_path.exists() and (now - last) < _RECHECK_INTERVAL_SECONDS:
            return

        try:
            async with httpx.AsyncClient(
                timeout=30.0, headers=_HTTP_HEADERS, follow_redirects=True
            ) as client:
                resp = await client.get(_RELEASES_API)
                resp.raise_for_status()
                releases = resp.json()

                asset_info = None
                version    = None
                for release in releases:
                    tag = release.get("tag_name", "")
                    if not tag.startswith("epg-cache-"):
                        continue
                    a = next((a for a in release.get("assets", []) if a["name"] == asset), None)
                    if a:
                        asset_info, version = a, tag
                        break

                if not asset_info:
                    logger.warning("[epg_guru_cache] no release asset %s found — redirect disabled for %s/%s",
                                    asset, market, tier)
                    return

                url = asset_info["browser_download_url"]
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                gz_tmp = local_path.with_suffix(".gz.tmp")

                async with client.stream("GET", url, timeout=180.0) as r:
                    r.raise_for_status()
                    with open(gz_tmp, "wb") as f:
                        async for chunk in r.aiter_bytes(65536):
                            f.write(chunk)

                sqlite_tmp = local_path.with_suffix(".tmp")
                with gzip.open(gz_tmp, "rb") as f_in, open(sqlite_tmp, "wb") as f_out:
                    while True:
                        block = f_in.read(1024 * 1024)
                        if not block:
                            break
                        f_out.write(block)
                gz_tmp.unlink()
                sqlite_tmp.replace(local_path)
                logger.info("[epg_guru_cache] updated %s/%s to %s", market, tier, version)

        except Exception as exc:
            logger.warning("[epg_guru_cache] refresh failed for %s/%s (will keep using existing cache if any): %s",
                            market, tier, exc)
        finally:
            _last_check_at[asset] = time.monotonic()


def _fmt_xmltv(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def _parse_xmltv_dt(s: str) -> Optional[datetime]:
    try:
        parts = s.strip().split()
        dt = datetime.strptime(parts[0][:14], "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def get_now_playing_for_source(
    source_url: str, window_start: datetime, window_end: datetime
) -> Optional[tuple[dict[str, dict], dict[str, str]]]:
    """Returns (programs, station_ids) for every channel in this source, in the
    same shape _parse_programmes_full produces — or None if the cache isn't
    available (caller should fall back to the direct-fetch path in that case).
    """
    market_tier = _URL_TO_MARKET_TIER.get(source_url)
    if market_tier is None:
        return None
    market, tier = market_tier

    await _maybe_refresh(market, tier)
    local_path = _local_path(_asset_name(market, tier))
    if not local_path.exists():
        return None

    import sqlite3

    now = datetime.now(timezone.utc)
    start_bound = _fmt_xmltv(window_start)
    end_bound   = _fmt_xmltv(window_end)

    try:
        conn = sqlite3.connect(str(local_path))
        rows = conn.execute(
            "SELECT tvg_id, start_utc, stop_utc, title, description FROM programmes "
            "WHERE start_utc < ? AND stop_utc > ?",
            (end_bound, start_bound),
        ).fetchall()
        station_rows = conn.execute("SELECT tvg_id, tvc_guide_stationid FROM channels").fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("[epg_guru_cache] query failed for %s/%s: %s", market, tier, exc)
        return None

    current:  dict[str, dict] = {}
    upcoming: dict[str, dict] = {}
    for tvg_id, start_s, stop_s, title, description in rows:
        start_dt = _parse_xmltv_dt(start_s)
        stop_dt  = _parse_xmltv_dt(stop_s)
        if not (start_dt and stop_dt):
            continue
        base = {"title": title or "", "start": start_dt.isoformat(), "stop": stop_dt.isoformat(),
                "description": description or ""}
        if start_dt <= now < stop_dt:
            current[tvg_id] = {**base, "_start_dt": start_dt}
        elif start_dt > now:
            prev = upcoming.get(tvg_id)
            if prev is None or start_dt < prev["_start_dt"]:
                upcoming[tvg_id] = {**base, "_start_dt": start_dt, "upcoming": True}

    programs = {**upcoming, **current}
    for v in programs.values():
        v.pop("_start_dt", None)

    station_ids = {tvg_id: sid for tvg_id, sid in station_rows if sid}
    return programs, station_ids
