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
import epg_guru_cache
from config import DATA_DIR, get_epg_settings

logger = logging.getLogger(__name__)

# Tried a ProcessPoolExecutor here to fully escape GIL contention during
# CPU-bound XML parsing — abandoned. In this environment, spawning a fresh
# worker process was itself unreliably slow (observed 30s+ timeouts just to
# start a no-op worker), which is worse than the problem it solved and even
# crashed startup outright. Sticking with the default thread pool.
#
# _WARM_SEMAPHORE staggers sources to ONE at a time (real-world reports from
# users with many/large XMLTV sources: repeated OOM kills). Real XMLTV files
# can be very large uncompressed, and ET.iterparse — even with .clear() per
# element — still retains per-element tree overhead as it parses. Warming
# multiple large sources concurrently stacks their peak memory, and CPython's
# allocator doesn't reliably hand freed memory back to the OS after a spike,
# so RSS ratchets upward cycle over cycle instead of returning to baseline.
# Staggering caps the worst case to one source's peak instead of N stacked.
_WARM_SEMAPHORE = asyncio.Semaphore(1)

# Wall-clock budget for parsing a single source, enforced cooperatively inside
# the parse loop itself (see _parse_programmes_full's `deadline` param). Sized
# generously so legitimate full multi-day EPG grids (observed: up to ~28M
# elements at ~240K elements/sec ≈ 115s) can actually finish, not just get
# partial credit — this is a backstop against runaway/corrupt sources, not a
# budget meant to routinely cut off real ones.
_PARSE_TIMEOUT_SECONDS = 180

# Wall-clock budget for downloading a single source (separate from parsing —
# see _fetch_and_cache). A steadily-trickling large response can otherwise run
# indefinitely without ever tripping httpx's own per-read timeout. Generous
# enough for a 300-400MB compressed download even on a slow connection.
_FETCH_TIMEOUT_SECONDS = 120

# Sources at or under this compressed size skip the warm semaphore entirely —
# their individual memory footprint is negligible, so serializing them behind
# a handful of huge sources only makes every source's warm-up wait in line for
# no benefit. Only sources above this (or with unknown/chunked length) queue
# behind _WARM_SEMAPHORE, since that's what actually bounds peak memory.
_LARGE_SOURCE_THRESHOLD_BYTES = 20 * 1024 * 1024

_CACHE_FILE = DATA_DIR / "epg_cache.json"
_CACHE:    dict[int, "_CacheEntry"]  = {}
_BG_TASKS: set[asyncio.Task]         = set()
_WARMING:  set[int]                  = set()
_ERRORS:   dict[int, str]            = {}
_NAMES:    dict[int, str]            = {}


class _CacheEntry:
    __slots__ = ("programs", "station_ids", "expires_at", "etag", "last_modified")

    def __init__(
        self,
        programs:      dict[str, dict],
        station_ids:   dict[str, str],
        expires_at:    float,
        etag:          Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> None:
        self.programs      = programs
        self.station_ids   = station_ids  # tvg_id → tvc_guide_stationid (from <channel> elements)
        self.expires_at    = expires_at
        self.etag          = etag
        self.last_modified = last_modified

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


def _open_xmltv(content: bytes, url: str):
    """Return a readable file-like for the XMLTV content.

    httpx transparently decompresses Content-Encoding: gzip (sent whenever we
    signal Accept-Encoding: gzip, which httpx does by default), so `content`
    here is already plain bytes whenever that happened — regardless of what
    the URL's extension claims. Some CDNs (observed: Cloudflare in front of
    epg.guru) even serve a *decompressed* body with no Content-Encoding header
    at all for clients that skip Accept-Encoding, while keeping a stale
    Content-Type: application/x-gzip — so the URL suffix is not a reliable
    signal either way. Trust magic bytes only; a `.gz`-suffixed URL whose
    content doesn't start with the gzip magic is just plain XML.
    """
    url_l = url.lower().split("?")[0]
    is_gz = content[:2] == b"\x1f\x8b"
    if is_gz:
        try:
            return gzip.open(io.BytesIO(content))
        except Exception:
            pass
    is_zip = url_l.endswith(".zip") or content[:4] == b"PK\x03\x04"
    if is_zip:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                xmls = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                return io.BytesIO(zf.read(xmls[0] if xmls else zf.namelist()[0]))
        except Exception:
            pass
    return io.BytesIO(content)


def _parse_programmes_full(
    fileobj,
    window_start: datetime,
    window_end: datetime,
    deadline: Optional[float] = None,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Single-pass parse: returns (now_playing dict, station_ids dict).

    fileobj is a readable file-like (may be a streaming GzipFile).
    station_ids maps tvg_id → tvc_guide_stationid extracted from <channel> elements.
    now_playing holds current + soonest upcoming program per tvg_id.

    deadline is a time.monotonic() cutoff checked periodically during the parse.
    This runs in a plain thread-pool worker thread, so asyncio.wait_for() around
    it can only stop *waiting* — it cannot stop the thread itself, and a
    GIL-starved event loop may not even notice the timeout promptly (observed:
    a corrupt/oversized source ran 12+ minutes past its supposed 45s cap before
    the outer wait_for ever got a chance to fire). Checking the deadline from
    inside the parse loop itself is what actually bounds worst-case memory/CPU.
    """
    now          = datetime.now(timezone.utc)
    current:      dict[str, dict] = {}
    upcoming:     dict[str, dict] = {}
    station_ids:  dict[str, str]  = {}
    elements_seen = 0

    try:
        context = iter(ET.iterparse(fileobj, events=("start", "end")))
        try:
            _, root = next(context)
        except StopIteration:
            root = None

        for event, elem in context:
            if event != "end":
                continue

            elements_seen += 1
            if elements_seen % 500 == 0:
                # This runs in a thread-pool worker, competing for the GIL with the
                # main event loop thread. A tight, allocation-heavy loop over millions
                # of elements can hog the GIL badly enough to starve the event loop
                # entirely — observed: unrelated small sources stalled 14+ minutes,
                # not just this source's own deadline failing to fire promptly.
                # time.sleep(0) forces an explicit GIL release here, far more reliable
                # than waiting on Python's default switch-interval during a hot loop.
                time.sleep(0)
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError(f"XMLTV parse exceeded time budget after {elements_seen} elements")

            if elem.tag == "channel":
                tvg_id = elem.get("id", "").strip()
                tvc_el = elem.find("tvc-guide-stationid")
                if tvg_id and tvc_el is not None and tvc_el.text:
                    station_ids[tvg_id] = tvc_el.text.strip()
                elem.clear()
                if root is not None:
                    root.clear()
                continue
            if elem.tag != "programme":
                # A child of an in-progress <programme>/<channel> (title, desc,
                # category, etc.) — its own "end" event fires before its parent's.
                # Do NOT clear it here: that would wipe its .text before the
                # parent's own "end" handler below gets a chance to read it via
                # elem.find(...). The parent's elem.clear() already removes all
                # of its children once the parent itself closes, so nothing is
                # leaked by leaving these alone in the meantime.
                continue
            tvg_id   = elem.get("channel", "").strip()
            start_dt = _parse_xmltv_dt(elem.get("start", ""))
            stop_dt  = _parse_xmltv_dt(elem.get("stop", ""))
            if not (tvg_id and start_dt and stop_dt):
                elem.clear()
                if root is not None:
                    root.clear()
                continue
            if start_dt >= window_end or stop_dt <= window_start:
                elem.clear()
                if root is not None:
                    root.clear()
                continue
            title_el = elem.find("title")
            desc_el  = elem.find("desc")
            base = {
                "title":       (title_el.text or "") if title_el is not None else "",
                "start":       start_dt.isoformat(),
                "stop":        stop_dt.isoformat(),
                "description": ((desc_el.text or "") if desc_el is not None else "")[:300],
            }
            if start_dt <= now < stop_dt:
                current[tvg_id] = {**base, "_start_dt": start_dt}
            elif start_dt > now:
                prev = upcoming.get(tvg_id)
                if prev is None or start_dt < prev["_start_dt"]:
                    upcoming[tvg_id] = {**base, "_start_dt": start_dt, "upcoming": True}
            elem.clear()
            if root is not None:
                root.clear()
    except ET.ParseError as exc:
        logger.warning("[xmltv_cache] XML parse error: %s", exc)

    merged = {**upcoming, **current}
    for v in merged.values():
        v.pop("_start_dt", None)
    return merged, station_ids


def _decompress_and_parse(
    content: bytes,
    url: str,
    window_start: datetime,
    window_end: datetime,
    timeout_seconds: float,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Combines decompress + parse into one call for a single run_in_executor hop."""
    fileobj  = _open_xmltv(content, url)
    deadline = time.monotonic() + timeout_seconds
    return _parse_programmes_full(fileobj, window_start, window_end, deadline)


def _persist_cache() -> None:
    """Write cache to disk using wall-clock expiry so it survives restarts."""
    try:
        now_mono = time.monotonic()
        now_real = time.time()
        data = {}
        for sid, entry in _CACHE.items():
            if entry.is_valid():
                data[str(sid)] = {
                    "expires_at":    now_real + (entry.expires_at - now_mono),
                    "programs":      entry.programs,
                    "station_ids":   entry.station_ids,
                    "etag":          entry.etag,
                    "last_modified": entry.last_modified,
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
            _CACHE[int(sid_str)] = _CacheEntry(
                entry_data["programs"],
                entry_data.get("station_ids", {}),
                mono_exp,
                entry_data.get("etag"),
                entry_data.get("last_modified"),
            )
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

    _WARMING.add(source_id)
    _ERRORS.pop(source_id, None)
    try:
        # Exact-match redirect for known-huge epg.guru sources — see
        # epg_guru_cache.py's module docstring for why this exists and why the
        # match is exact, never fuzzy. Falls through to the normal direct-fetch
        # path below if the pre-parsed cache isn't available for any reason.
        if epg_guru_cache.is_known_url(url):
            redirected = await epg_guru_cache.get_now_playing_for_source(url, window_start, window_end)
            if redirected is not None:
                programs, station_ids = redirected
                logger.info("[xmltv_cache] source=%d → %d now-playing (epg.guru cache redirect)",
                            source_id, len(programs))
                _CACHE[source_id] = _CacheEntry(programs, station_ids, time.monotonic() + ttl)
                _persist_cache()
                return
            logger.info("[xmltv_cache] source=%d epg.guru cache unavailable, falling back to direct fetch",
                        source_id)

        # Send conditional headers if we have cached validators — server returns 304
        # Not Modified instead of re-sending the full file when nothing has changed.
        existing    = _CACHE.get(source_id)
        req_headers: dict[str, str] = {}
        if existing:
            if existing.etag:
                req_headers["If-None-Match"] = existing.etag
            elif existing.last_modified:
                req_headers["If-Modified-Since"] = existing.last_modified

        logger.info("[xmltv_cache] fetching source=%d url=%s%s",
                    source_id, url, " (conditional)" if req_headers else "")

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            async with http.stream("GET", url, headers=req_headers) as stream_resp:
                if stream_resp.status_code == 304 and existing:
                    existing.expires_at = time.monotonic() + ttl
                    _persist_cache()
                    logger.info("[xmltv_cache] source=%d not modified (304) — TTL extended, no re-parse",
                                source_id)
                    return

                stream_resp.raise_for_status()
                etag          = stream_resp.headers.get("etag")
                last_modified = stream_resp.headers.get("last-modified")

                # Only serialize LARGE sources against each other — that's what
                # prevents concurrent big parses from stacking peak memory (the
                # original OOM cause). Small sources skip the semaphore entirely so
                # they're never stuck waiting behind a huge source's turn.
                content_length = int(stream_resp.headers.get("content-length") or 0)
                is_large = content_length == 0 or content_length > _LARGE_SOURCE_THRESHOLD_BYTES

                if is_large:
                    async with _WARM_SEMAPHORE:
                        content, programs, station_ids = await _drain_and_parse(
                            source_id, stream_resp, url, window_start, window_end
                        )
                else:
                    content, programs, station_ids = await _drain_and_parse(
                        source_id, stream_resp, url, window_start, window_end
                    )

        logger.info("[xmltv_cache] source=%d → %d now-playing, %d station IDs cached%s",
                    source_id, len(programs), len(station_ids),
                    f" [etag={etag}]" if etag else "")
        _CACHE[source_id] = _CacheEntry(
            programs, station_ids, time.monotonic() + ttl, etag, last_modified
        )
        _persist_cache()
    except Exception as exc:
        logger.error("[xmltv_cache] source=%d fetch failed: %s", source_id, exc)
        _ERRORS[source_id] = str(exc)
    finally:
        _WARMING.discard(source_id)


async def _drain_and_parse(source_id, stream_resp, url, window_start, window_end):
    """Read the streamed body under our own deadline, then parse it.

    asyncio.wait_for() around a single opaque `await http.get(...)` is not
    reliable: cancellation is cooperative, and once httpx is suspended deep
    inside a socket read on a slow-but-still-trickling connection, the
    CancelledError doesn't get delivered until that read naturally completes
    — observed taking 400+ seconds on a 60s budget for a slow source. Checking
    our own deadline after every chunk enforces the wall-clock cap ourselves
    instead of relying on cancelling httpx from the outside.
    """
    _fetch_deadline = time.monotonic() + _FETCH_TIMEOUT_SECONDS
    chunks: list[bytes] = []
    async for chunk in stream_resp.aiter_bytes():
        chunks.append(chunk)
        if time.monotonic() > _fetch_deadline:
            raise TimeoutError(
                f"download exceeded {_FETCH_TIMEOUT_SECONDS}s budget "
                f"after {sum(len(c) for c in chunks)} bytes"
            )
    content = b"".join(chunks)

    loop = asyncio.get_event_loop()
    # Hard cap on the CPU-bound step. The real enforcement is the `deadline`
    # checked inside the parse loop itself (_parse_programmes_full) — a plain
    # thread-pool worker can't be interrupted from outside, and wait_for alone
    # was observed to never fire in practice (a GIL-starved event loop didn't
    # get scheduled in time to notice). wait_for here is just a backstop with
    # slack above the cooperative deadline, in case the in-loop check is
    # somehow skipped.
    programs, station_ids = await asyncio.wait_for(
        loop.run_in_executor(
            None, _decompress_and_parse, content, url, window_start, window_end, _PARSE_TIMEOUT_SECONDS
        ),
        timeout=_PARSE_TIMEOUT_SECONDS + 15,
    )
    return content, programs, station_ids


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



def get_station_id(source_id: int, tvg_id: str) -> Optional[str]:
    """Return tvc_guide_stationid for a tvg_id from a specific source's channel elements, or None."""
    entry = _CACHE.get(source_id)
    if not entry or not entry.is_valid():
        return None
    return entry.station_ids.get(tvg_id) or None


def get_cold_source_ids(source_ids: set[int]) -> set[int]:
    """Return source_ids that need warming: cache missing/expired AND not already in progress."""
    return {
        sid for sid in source_ids
        if sid not in _WARMING and (not (e := _CACHE.get(sid)) or not e.is_valid())
    }


def is_any_warming(source_ids: set[int]) -> bool:
    """True if any of these source_ids are currently being fetched."""
    return bool(source_ids & _WARMING)


def get_now_playing(source_ids: list[int], tvg_id: str) -> Optional[dict]:
    for source_id in source_ids:
        entry = _CACHE.get(source_id)
        if entry and entry.is_valid():
            program = entry.programs.get(tvg_id)
            if program:
                return program
    return None


def clear_xmltv_cache() -> None:
    """Expire all XMLTV cache entries so the next warm fetches fresh data."""
    _CACHE.clear()
    _ERRORS.clear()
    try:
        if _CACHE_FILE.exists():
            _CACHE_FILE.unlink()
    except Exception:
        pass
    logger.info("[xmltv_cache] cache cleared — next warm will re-fetch all sources")


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


# ── Dispatcharr EPG grid cache ────────────────────────────────────────────────
# Fetches /api/epg/grid/ and /api/epg/epgdata/ (API-key authenticated JSON),
# cached 30 min. Commit always calls invalidate_guide_cache() for a fresh load.

_GUIDE_CACHE:       Optional[dict] = None
_GUIDE_CACHE_TIME:  float          = 0.0
_EPGDATA_CACHE:     Optional[dict] = None
_EPGDATA_CACHE_TIME: float         = 0.0
GUIDE_CACHE_TTL    = 1800.0  # 30 minutes


async def fetch_dispatcharr_grid(client) -> dict:
    """Return cached grid data or fetch /api/epg/grid/ from Dispatcharr."""
    global _GUIDE_CACHE, _GUIDE_CACHE_TIME
    if _GUIDE_CACHE and time.monotonic() - _GUIDE_CACHE_TIME < GUIDE_CACHE_TTL:
        return _GUIDE_CACHE
    raw     = await client.get("/api/epg/grid/")
    entries = raw.get("data", []) if isinstance(raw, dict) else []
    programs: dict[str, list] = {}
    for e in entries:
        tvg_id = (e.get("tvg_id") or "").strip()
        if not tvg_id:
            continue
        programs.setdefault(tvg_id, []).append({
            "title":       e.get("title") or "",
            "start":       e.get("start_time") or "",
            "stop":        e.get("end_time") or "",
            "description": (e.get("description") or "")[:300],
        })
    for progs in programs.values():
        progs.sort(key=lambda p: p["start"])
    data = {"programs": programs}
    _GUIDE_CACHE      = data
    _GUIDE_CACHE_TIME = time.monotonic()
    logger.info("[epg_grid] cached %d tvg_ids, %d total programs",
                len(programs), sum(len(v) for v in programs.values()))
    return data


async def fetch_dispatcharr_epgdata(client) -> dict:
    """Return cached epg_data_id → tvg_id map or fetch /api/epg/epgdata/."""
    global _EPGDATA_CACHE, _EPGDATA_CACHE_TIME
    if _EPGDATA_CACHE is not None and time.monotonic() - _EPGDATA_CACHE_TIME < GUIDE_CACHE_TTL:
        return _EPGDATA_CACHE
    raw     = await client.get("/api/epg/epgdata/")
    entries = raw if isinstance(raw, list) else raw.get("results", [])
    epgdata_map: dict[int, dict] = {
        int(e["id"]): {
            "tvg_id":   e["tvg_id"].strip(),
            "icon_url": e.get("icon_url") or "",
        }
        for e in entries
        if e.get("id") and e.get("tvg_id")
    }
    _EPGDATA_CACHE      = epgdata_map
    _EPGDATA_CACHE_TIME = time.monotonic()
    logger.info("[epg_grid] cached epgdata map: %d entries", len(epgdata_map))
    return epgdata_map


def invalidate_guide_cache() -> None:
    """Force next guide request to re-fetch from Dispatcharr (called after EPG commit)."""
    global _GUIDE_CACHE_TIME, _EPGDATA_CACHE_TIME
    _GUIDE_CACHE_TIME   = 0.0
    _EPGDATA_CACHE_TIME = 0.0
