"""
XMLTV Programme Cache — Now Playing support

On each match run, selected EPG source URLs are fetched directly,
decompressed (gz / zip / plain), and stream-parsed via iterparse().
Only programmes overlapping the configured window around now are kept.
Results are cached in-memory per source_id; TTL and window are read
from config at fetch time so settings changes take effect on next warm.
"""

import asyncio
import gzip
import io
import json
import logging
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from config import DATA_DIR, get_epg_settings

logger = logging.getLogger(__name__)

_CACHE_FILE = DATA_DIR / "epg_cache.json"
_CACHE:    dict[int, "_CacheEntry"]  = {}
_BG_TASKS: set[asyncio.Task]         = set()
_WARMING:  set[int]                  = set()
_ERRORS:   dict[int, str]            = {}
_NAMES:    dict[int, str]            = {}


class _CacheEntry:
    __slots__ = ("programs", "expires_at")

    def __init__(self, programs: dict[str, dict], expires_at: float) -> None:
        self.programs   = programs
        self.expires_at = expires_at

    def is_valid(self) -> bool:
        return time.monotonic() < self.expires_at


def _parse_xmltv_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        parts = s.strip().split()
        dt    = datetime.strptime(parts[0][:14], "%Y%m%d%H%M%S")
        if len(parts) > 1:
            tz_raw = parts[1]
            sign   = 1 if tz_raw[0] == "+" else -1
            hh, mm = int(tz_raw[1:3]), int(tz_raw[3:5]) if len(tz_raw) >= 5 else 0
            offset = timedelta(hours=hh, minutes=mm) * sign
        else:
            offset = timedelta(0)
        return dt.replace(tzinfo=timezone(offset))
    except Exception:
        return None


def _decompress(content: bytes, url: str, enc_header: str) -> bytes:
    if "gzip" in enc_header:
        try:
            return gzip.decompress(content)
        except Exception:
            pass
    url_l = url.lower().split("?")[0]
    if url_l.endswith(".gz") or url_l.endswith(".xml.gz"):
        try:
            return gzip.decompress(content)
        except Exception:
            pass
    if url_l.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                xmls = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                return zf.read(xmls[0] if xmls else zf.namelist()[0])
        except Exception:
            pass
    if content[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(content)
        except Exception:
            pass
    if content[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                xmls = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                return zf.read(xmls[0] if xmls else zf.namelist()[0])
        except Exception:
            pass
    return content


def _parse_programmes(raw: bytes, window_start: datetime, window_end: datetime) -> dict[str, dict]:
    now      = datetime.now(timezone.utc)
    current  = {}
    upcoming = {}

    try:
        for _, elem in ET.iterparse(io.BytesIO(raw), events=("end",)):
            if elem.tag != "programme":
                continue
            tvg_id   = elem.get("channel", "").strip()
            start_dt = _parse_xmltv_dt(elem.get("start", ""))
            stop_dt  = _parse_xmltv_dt(elem.get("stop", ""))
            if not (tvg_id and start_dt and stop_dt):
                elem.clear()
                continue
            if start_dt > window_end or stop_dt < window_start:
                elem.clear()
                continue
            title_el = elem.find("title")
            desc_el  = elem.find("desc")
            desc_raw = (desc_el.text or "") if desc_el is not None else ""
            entry = {
                "title":       (title_el.text or "") if title_el is not None else "",
                "start":       start_dt.isoformat(),
                "stop":        stop_dt.isoformat(),
                "description": desc_raw[:300],
                "_start_dt":   start_dt,
            }
            if start_dt <= now < stop_dt:
                current[tvg_id] = entry
            elif start_dt > now:
                prev = upcoming.get(tvg_id)
                if prev is None or start_dt < prev["_start_dt"]:
                    upcoming[tvg_id] = {**entry, "upcoming": True}
            elem.clear()
    except ET.ParseError as exc:
        logger.warning("[xmltv_cache] XML parse error: %s", exc)

    merged = {**upcoming, **current}
    for v in merged.values():
        v.pop("_start_dt", None)
    return merged


def _persist_cache() -> None:
    """Write cache to disk using wall-clock expiry so it survives restarts."""
    try:
        now_mono = time.monotonic()
        now_real = time.time()
        data = {}
        for sid, entry in _CACHE.items():
            if entry.is_valid():
                data[str(sid)] = {
                    "expires_at": now_real + (entry.expires_at - now_mono),
                    "programs":   entry.programs,
                }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data))
    except Exception as exc:
        logger.warning("[xmltv_cache] failed to persist cache: %s", exc)


def _restore_cache() -> None:
    """Load persisted cache from disk on startup."""
    if not _CACHE_FILE.exists():
        return
    try:
        now_mono = time.monotonic()
        now_real = time.time()
        data     = json.loads(_CACHE_FILE.read_text())
        loaded   = 0
        for sid_str, entry_data in data.items():
            real_exp = float(entry_data["expires_at"])
            if real_exp <= now_real:
                continue
            mono_exp = now_mono + (real_exp - now_real)
            _CACHE[int(sid_str)] = _CacheEntry(entry_data["programs"], mono_exp)
            loaded += 1
        if loaded:
            logger.info("[xmltv_cache] restored %d source(s) from disk cache", loaded)
    except Exception as exc:
        logger.warning("[xmltv_cache] failed to restore cache: %s", exc)


async def _fetch_and_cache(source_id: int, url: str) -> None:
    s             = get_epg_settings()
    ttl           = int(s["epg_cache_ttl_hours"] * 3600)
    window_before = timedelta(hours=s["epg_window_hours_before"])
    window_after  = timedelta(hours=s["epg_window_hours_after"])

    now          = datetime.now(timezone.utc)
    window_start = now - window_before
    window_end   = now + window_after

    logger.info("[xmltv_cache] fetching source=%d url=%s window=-%s/+%s ttl=%ds",
                source_id, url, window_before, window_after, ttl)
    _WARMING.add(source_id)
    _ERRORS.pop(source_id, None)
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as http:
            resp = await http.get(url)
            resp.raise_for_status()
        enc  = resp.headers.get("content-encoding", "")
        loop = asyncio.get_event_loop()
        raw      = await loop.run_in_executor(None, _decompress, resp.content, url, enc)
        programs = await loop.run_in_executor(None, _parse_programmes, raw, window_start, window_end)
        logger.info("[xmltv_cache] source=%d → %d programmes cached", source_id, len(programs))
        _CACHE[source_id] = _CacheEntry(programs, time.monotonic() + ttl)
        _persist_cache()
    except Exception as exc:
        logger.error("[xmltv_cache] source=%d fetch failed: %s", source_id, exc)
        _ERRORS[source_id] = str(exc)
    finally:
        _WARMING.discard(source_id)


async def warm_cache(source_url_map: dict[int, str], source_names: dict[int, str] | None = None) -> None:
    if source_names:
        _NAMES.update(source_names)
    tasks = []
    for source_id, url in source_url_map.items():
        if not url:
            continue
        entry = _CACHE.get(source_id)
        if entry and entry.is_valid():
            logger.info("[xmltv_cache] source=%d still valid, skipping", source_id)
            continue
        tasks.append(_fetch_and_cache(source_id, url))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def fire_warm_cache(source_url_map: dict[int, str]) -> None:
    task = asyncio.create_task(warm_cache(source_url_map))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def get_now_playing(source_ids: list[int], tvg_id: str) -> Optional[dict]:
    for source_id in source_ids:
        entry = _CACHE.get(source_id)
        if entry and entry.is_valid():
            program = entry.programs.get(tvg_id)
            if program:
                return program
    return None


def cache_status(source_ids: list[int]) -> dict:
    return {
        sid: ("ready" if (e := _CACHE.get(sid)) and e.is_valid() else "loading")
        for sid in source_ids
    }


def warm_status() -> dict:
    """Global EPG warming status for the UI status indicator."""
    ready_ids   = {sid for sid, e in _CACHE.items() if e.is_valid()}
    warming_ids = set(_WARMING)
    error_ids   = set(_ERRORS)
    all_ids     = ready_ids | warming_ids | error_ids

    def _status(sid: int) -> str:
        if sid in warming_ids: return "warming"
        if sid in error_ids:   return "error"
        if sid in ready_ids:   return "ready"
        return "pending"

    sources = [
        {"id": sid, "name": _NAMES.get(sid, f"Source {sid}"), "status": _status(sid)}
        for sid in sorted(all_ids)
    ]

    return {
        "total":     len(all_ids),
        "ready":     len(ready_ids),
        "warming":   len(warming_ids),
        "errors":    len(error_ids),
        "all_ready": len(all_ids) > 0 and len(warming_ids) == 0,
        "idle":      len(all_ids) == 0 and len(warming_ids) == 0,
        "sources":   sources,
    }


_restore_cache()
