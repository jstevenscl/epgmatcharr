import asyncio
import json
import logging
import os
import re as _re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urljoin

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from auth import create_session, revoke_session, verify_session
from config import (
    config_from_env, get_config, get_epg_settings, has_credentials,
    is_configured, save_config, save_epg_settings, set_credentials,
    verify_credentials,
)
from dispatcharr_client import DispatcharrClient
from epg_cache import cache_status as _cache_status, clear_xmltv_cache, fetch_dispatcharr_epgdata, fetch_dispatcharr_grid, fire_warm_cache, get_cold_source_ids, get_now_playing, get_station_id, invalidate_guide_cache, is_any_warming, warm_status as _warm_status
from gn_station_db import get_status as _gn_db_status, lookup_gn_id, start_update as _start_gn_db_update
from epg_matcher_service import fetch_channels, fetch_epg_data as _fetch_all_epg_data, run_match, search_epg
import log_buffer as _log_buffer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["epg-matcher"])


# ── Guards ────────────────────────────────────────────────────────────────────

async def require_configured():
    if not is_configured():
        raise HTTPException(503, detail="not_configured")


async def require_auth(x_session_token: Optional[str] = Header(None, alias="X-Session-Token")):
    if not has_credentials():
        return  # no credentials configured — auth not enforced yet
    if not x_session_token or not verify_session(x_session_token):
        raise HTTPException(401, detail="unauthorized")


# ── Request models ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class CredentialsRequest(BaseModel):
    username: str
    password: str


class SettingsRequest(BaseModel):
    dispatcharr_url:   str
    dispatcharr_token: str


class EpgSettingsRequest(BaseModel):
    epg_cache_ttl_hours:     float = 1.0
    epg_window_hours_before: float = 0.5
    epg_window_hours_after:  float = 3.0
    guide_window_hours:      float = 2.0
    backfill_gn_id:      bool  = False


class MatchRequest(BaseModel):
    source_ids:      list[int]        = []
    channel_ids:     Optional[list[int]] = None
    unassigned_only: bool             = True
    group_id:        Optional[int]    = None
    tvg_id_filter:   Optional[str]    = None


class NowPlayingRequest(BaseModel):
    epg_data_id: int
    tvg_id:      Optional[str] = None
    source_ids:  list[int]     = []


class EpgAssociation(BaseModel):
    channel_id:  int
    epg_data_id: Optional[int] = None


class NameChange(BaseModel):
    channel_id: int
    name:       str


class CommitRequest(BaseModel):
    associations: list[EpgAssociation]
    name_changes: list[NameChange] = []


# ── Auth endpoints (no auth required) ────────────────────────────────────────

@router.post("/auth/login/")
async def login(body: LoginRequest):
    if not verify_credentials(body.username, body.password):
        raise HTTPException(401, detail="Invalid username or password")
    return {"token": create_session()}


@router.get("/auth/verify/")
async def auth_verify(x_session_token: Optional[str] = Header(None, alias="X-Session-Token")):
    if not has_credentials():
        return {"valid": True, "no_credentials": True}
    return {"valid": bool(x_session_token and verify_session(x_session_token))}


@router.post("/auth/logout/")
async def logout(x_session_token: Optional[str] = Header(None, alias="X-Session-Token")):
    if x_session_token:
        revoke_session(x_session_token)
    return {"ok": True}


# ── Settings endpoints ────────────────────────────────────────────────────────

@router.get("/settings/")
async def get_settings():
    url, token = get_config()
    epg        = get_epg_settings()
    return {
        "configured":              bool(url and token),
        "dispatcharr_url":         url,
        "has_token":               bool(token),
        "from_env":                config_from_env(),
        "has_credentials":         has_credentials(),
        "epg_cache_ttl_hours":     epg["epg_cache_ttl_hours"],
        "epg_window_hours_before": epg["epg_window_hours_before"],
        "epg_window_hours_after":  epg["epg_window_hours_after"],
        "guide_window_hours":      epg["guide_window_hours"],
        "backfill_gn_id":      epg["backfill_gn_id"],
    }


@router.post("/settings/")
async def save_settings(body: SettingsRequest):
    if config_from_env():
        raise HTTPException(400, detail="Configuration is managed via environment variables and cannot be changed here.")
    if not body.dispatcharr_url.strip() or not body.dispatcharr_token.strip():
        raise HTTPException(400, detail="Both URL and token are required.")
    save_config(body.dispatcharr_url.strip(), body.dispatcharr_token.strip())
    return {"ok": True}


@router.post("/settings/test/")
async def test_connection(body: SettingsRequest):
    url = body.dispatcharr_url.rstrip("/").strip()
    if not url or not body.dispatcharr_token.strip():
        return {"ok": False, "message": "URL and token are required."}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{url}/api/channels/channels/",
                headers={"X-API-Key": body.dispatcharr_token.strip()},
                params={"page_size": 1},
            )
            if resp.status_code == 200:
                return {"ok": True, "message": "Connected successfully"}
            elif resp.status_code in (401, 403):
                return {"ok": False, "message": "Invalid API token"}
            else:
                return {"ok": False, "message": f"Unexpected response: HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "message": "Could not connect — check the URL"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "Connection timed out"}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@router.post("/settings/epg/", dependencies=[Depends(require_auth)])
async def save_epg_settings_endpoint(body: EpgSettingsRequest):
    save_epg_settings(body.epg_cache_ttl_hours, body.epg_window_hours_before, body.epg_window_hours_after, body.guide_window_hours, body.backfill_gn_id)
    return {"ok": True}


@router.post("/settings/credentials/")
async def set_credentials_endpoint(
    body: CredentialsRequest,
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token"),
):
    if has_credentials():
        # Allow if env var recovery mode is active, otherwise require session
        env_override = bool(
            os.environ.get("EPGMATCHARR_ADMIN_USER") and
            os.environ.get("EPGMATCHARR_ADMIN_PASSWORD")
        )
        if not env_override and not (x_session_token and verify_session(x_session_token)):
            raise HTTPException(401, detail="unauthorized")
    if not body.username.strip():
        raise HTTPException(400, detail="Username is required.")
    if len(body.password) < 6:
        raise HTTPException(400, detail="Password must be at least 6 characters.")
    set_credentials(body.username.strip(), body.password)
    return {"ok": True}


# ── Version endpoint ──────────────────────────────────────────────────────────

@router.get("/version/")
async def get_version(request: Request):
    return {"version": request.app.version}


# ── Config endpoint ───────────────────────────────────────────────────────────

@router.get("/config/")
async def get_config_endpoint():
    url, _ = get_config()
    return {"dispatcharr_url": url, "configured": bool(url)}


# ── Disconnect endpoint ───────────────────────────────────────────────────────

@router.post("/settings/disconnect/")
async def disconnect(x_session_token: Optional[str] = Header(None, alias="X-Session-Token")):
    if has_credentials() and not (x_session_token and verify_session(x_session_token)):
        raise HTTPException(401, detail="unauthorized")
    save_config("", "")
    return {"ok": True}


# ── Stream proxy ──────────────────────────────────────────────────────────────

def _rewrite_m3u8(content: str, base_url: str) -> str:
    def rewrite_uri(m: _re.Match) -> str:
        uri     = m.group(1)
        abs_uri = uri if uri.startswith("http") else urljoin(base_url, uri)
        return f'URI="/api/stream-segment?url={quote(abs_uri, safe="")}"'

    lines = content.splitlines()
    out   = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(_re.sub(r'URI="([^"]+)"', rewrite_uri, line))
        else:
            abs_url = stripped if stripped.startswith("http") else urljoin(base_url, stripped)
            out.append(f"/api/stream-segment?url={quote(abs_url, safe='')}")
    return "\n".join(out)


async def _resolve_stream_url(channel_id: int) -> str:
    """Fetch the channel's first stream URL from Dispatcharr."""
    client = DispatcharrClient()
    try:
        ch      = await client.get(f"/api/channels/channels/{channel_id}/")
        streams = ch.get("streams", [])
        if isinstance(streams, list) and streams:
            first = streams[0]
            if isinstance(first, dict):
                return first.get("url", "")
            elif isinstance(first, int):
                s = await client.get(f"/api/channels/streams/{first}/")
                return s.get("url", "")
    except Exception as exc:
        logger.warning("[stream] channel %d detail fetch failed: %s", channel_id, exc)
    return ""


@router.get("/stream/{channel_id}")
async def stream_manifest(channel_id: int):
    """Returns HLS m3u8 (rewritten) or {type:ts} without fetching the stream."""
    stream_url = await _resolve_stream_url(channel_id)
    if not stream_url:
        raise HTTPException(404, detail="No stream URL found for this channel.")

    # Detect type from the source URL extension — avoids trying to buffer a live TS stream
    clean = stream_url.split("?")[0].lower()
    is_hls = clean.endswith(".m3u8")

    if is_hls:
        logger.info("[stream] channel_id=%d → HLS, fetching manifest…", channel_id)
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
                resp = await http.get(stream_url)
        except Exception as exc:
            raise HTTPException(502, detail=str(exc))
        if not resp.is_success:
            raise HTTPException(resp.status_code, detail=f"Manifest source returned HTTP {resp.status_code}")
        ct   = resp.headers.get("content-type", "")
        text = resp.text
        if "mpegurl" in ct.lower() or text.strip().startswith("#EXTM3U"):
            base = str(resp.url).rsplit("/", 1)[0] + "/"
            return PlainTextResponse(_rewrite_m3u8(text, base), media_type="application/vnd.apple.mpegurl")

    # TS stream (or unknown) — signal frontend to use the streaming endpoint
    logger.info("[stream] channel_id=%d → TS, url=%s…", channel_id, clean.rsplit("/", 1)[-1])
    return Response(
        status_code=200,
        media_type="application/json",
        content='{"type":"ts"}',
        headers={"X-Stream-Type": "ts"},
    )


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


@router.get("/stream-ts/{channel_id}")
async def stream_ts(channel_id: int, request: Request):
    """Remux MPEG-TS → fragmented MP4 via FFmpeg and stream to browser."""
    stream_url = await _resolve_stream_url(channel_id)
    if not stream_url:
        raise HTTPException(404, detail="No stream URL found for this channel.")

    logger.info("[stream-ts] channel_id=%d → ffmpeg fMP4 remux", channel_id)

    async def generate():
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-loglevel", "error",
            "-user_agent", _UA,
            "-i", stream_url,
            "-c", "copy",
            "-f", "mpegts",
            "-flush_packets", "1",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async def log_stderr():
            async for line in proc.stderr:
                logger.warning("[ffmpeg] ch%d: %s", channel_id, line.decode().rstrip())

        asyncio.ensure_future(log_stderr())
        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    return StreamingResponse(
        generate(),
        media_type="video/MP2T",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/stream-segment")
async def stream_segment(url: str = Query(...)):
    _, token = get_config()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"X-API-Key": token})
        return Response(content=resp.content, media_type=resp.headers.get("content-type", "video/MP2T"))
    except Exception as exc:
        raise HTTPException(502, detail=str(exc))


# ── Endpoints (require configured + auth) ─────────────────────────────────────

_GUARDS = [Depends(require_configured), Depends(require_auth)]


@router.get("/sources/", dependencies=_GUARDS)
async def get_sources():
    client = DispatcharrClient()
    raw = await client.get("/api/epg/sources/")
    return raw if isinstance(raw, list) else raw.get("results", [])


@router.get("/groups/", dependencies=_GUARDS)
async def get_groups():
    client = DispatcharrClient()
    raw    = await client.get("/api/channels/groups/")
    groups = raw if isinstance(raw, list) else raw.get("results", [])

    channels  = await fetch_channels(client)
    populated = {c.get("channel_group_id") for c in channels if c.get("channel_group_id")}
    return [g for g in groups if g.get("id") in populated]


@router.get("/channels/", dependencies=_GUARDS)
async def get_channels(
    group_ids:       Optional[str] = Query(None),
    unassigned_only: bool          = Query(False),
):
    client       = DispatcharrClient()
    all_channels = await fetch_channels(client)

    gids = [int(x) for x in group_ids.split(",") if x.strip().isdigit()] if group_ids else []
    if gids:
        all_channels = [c for c in all_channels if c.get("channel_group_id") in set(gids)]
    if unassigned_only:
        all_channels = [c for c in all_channels if not c.get("epg_data_id")]

    all_channels.sort(key=lambda c: (c.get("channel_number") or 99999))
    return {
        "results": [
            {
                "channel_id":       c.get("id"),
                "channel_name":     c.get("effective_name") or c.get("name", ""),
                "channel_number":   c.get("channel_number"),
                "channel_group_id": c.get("channel_group_id"),
                "channel_uuid":     c.get("uuid"),
                "has_epg":          bool(c.get("epg_data_id")),
                "epg_data_id":      c.get("epg_data_id"),
                "tvg_id":              c.get("effective_tvg_id") or c.get("tvg_id"),
                "tvc_guide_stationid": c.get("effective_tvc_guide_stationid") or c.get("tvc_guide_stationid"),
                "stream_count": (
                    len(c["streams"]) if isinstance(c.get("streams"), list)
                    else c["streams"] if isinstance(c.get("streams"), int)
                    else c.get("stream_count")
                ),
            }
            for c in all_channels
        ],
        "total": len(all_channels),
    }


async def _fetch_all_epg_data(client: DispatcharrClient) -> list[dict]:
    """Fetches all EPG data entries with pagination."""
    results: list[dict] = []
    page = 1
    while True:
        resp = await client.get("/api/epg/epgdata/", params={"page": page, "page_size": 500})
        if isinstance(resp, list):
            results.extend(resp)
            break
        results.extend(resp.get("results", []))
        if not resp.get("next"):
            break
        page += 1
    return results


@router.get("/assigned-epg-sources/", dependencies=_GUARDS)
async def get_assigned_epg_sources():
    """Returns EPG sources that have at least one channel assigned to them."""
    client = DispatcharrClient()
    channels, all_epg, sources_raw = await asyncio.gather(
        fetch_channels(client),
        _fetch_all_epg_data(client),
        client.get("/api/epg/sources/"),
    )

    assigned_ids  = {c.get("epg_data_id") for c in channels if c.get("epg_data_id")}
    all_sources   = sources_raw if isinstance(sources_raw, list) else sources_raw.get("results", [])
    source_names  = {s["id"]: s.get("name", f"Source {s['id']}") for s in all_sources}

    source_to_epg_ids: dict[int, list[int]] = {}
    for e in all_epg:
        eid = e.get("id")
        src = e.get("epg_source")
        if eid in assigned_ids and src:
            source_to_epg_ids.setdefault(src, []).append(eid)

    return sorted(
        [
            {
                "id":           sid,
                "name":         source_names.get(sid, f"Source {sid}"),
                "epg_data_ids": eids,
            }
            for sid, eids in source_to_epg_ids.items()
        ],
        key=lambda x: x["name"].lower(),
    )


@router.get("/profiles/", dependencies=_GUARDS)
async def get_profiles():
    client = DispatcharrClient()
    raw      = await client.get("/api/channels/profiles/")
    profiles = raw if isinstance(raw, list) else raw.get("results", [])
    return [{"id": p["id"], "name": p["name"]} for p in profiles if p.get("id") and p.get("name")]


@router.get("/guide/", dependencies=_GUARDS)
async def get_guide(
    hours:      float            = Query(2.0, ge=0.5, le=12.0),
    profile_id: Optional[int]   = Query(None),
):
    """EPG guide — all Dispatcharr channels sorted by channel number, programs from grid."""
    client = DispatcharrClient()

    coros = [
        fetch_dispatcharr_grid(client),
        client.get("/api/channels/channels/summary/"),
        fetch_dispatcharr_epgdata(client),
        client.get("/api/channels/groups/"),
    ]
    if profile_id:
        coros.append(client.get(f"/api/channels/profiles/{profile_id}/"))

    results     = await asyncio.gather(*coros)
    guide_data  = results[0]
    summary_raw = results[1]
    epgdata_map = results[2]
    groups_raw  = results[3]
    profile_raw = results[4] if profile_id else None

    # Build profile channel ID set for filtering
    profile_channel_ids: Optional[set[int]] = None
    if profile_raw and isinstance(profile_raw, dict):
        channels_field = profile_raw.get("channels")
        if channels_field is not None:
            if isinstance(channels_field, list):
                profile_channel_ids = {int(x) for x in channels_field if str(x).lstrip('-').isdigit()}
            elif isinstance(channels_field, str):
                try:
                    parsed = json.loads(channels_field)
                    if isinstance(parsed, list):
                        profile_channel_ids = {int(x) for x in parsed if str(x).lstrip('-').isdigit()}
                except Exception:
                    parts = [x.strip() for x in channels_field.split(",") if x.strip().lstrip('-').isdigit()]
                    if parts:
                        profile_channel_ids = {int(x) for x in parts}

    groups_list = groups_raw if isinstance(groups_raw, list) else groups_raw.get("results", [])
    group_map: dict[int, str] = {g["id"]: g["name"] for g in groups_list if g.get("id")}

    channels_raw = summary_raw if isinstance(summary_raw, list) else []

    channel_list = []
    for c in channels_raw:
        ch_id = c.get("id")
        if profile_channel_ids is not None and ch_id not in profile_channel_ids:
            continue
        epg_data_id  = c.get("epg_data_id")
        epgdata_entry = epgdata_map.get(epg_data_id, {}) if epg_data_id else {}
        tvg_id       = epgdata_entry.get("tvg_id", "") if isinstance(epgdata_entry, dict) else ""
        logo_url     = epgdata_entry.get("icon_url", "") if isinstance(epgdata_entry, dict) else ""
        if not tvg_id:
            tvg_id = c.get("uuid") or ""
        group_id = c.get("channel_group_id")
        has_epg  = bool(epg_data_id) or bool(tvg_id and tvg_id in guide_data["programs"])
        channel_list.append({
            "channel_id":       ch_id,
            "channel_name":     c.get("name") or "",
            "channel_number":   c.get("channel_number"),
            "channel_group":    group_map.get(group_id, "") if group_id else "",
            "channel_group_id": group_id,
            "tvg_id":           tvg_id,
            "logo_url":         logo_url,
            "has_epg":          has_epg,
            "has_stream":       True,
        })
    channel_list.sort(key=lambda ch: (ch["channel_number"] or 99999))

    now          = datetime.now(timezone.utc)
    window_start = (now - timedelta(hours=2)).isoformat()
    window_end   = (now + timedelta(hours=hours)).isoformat()

    programs: dict[str, list] = {}
    for tvg_id, progs in guide_data["programs"].items():
        filtered = [p for p in progs if p["stop"] > window_start and p["start"] < window_end]
        if filtered:
            programs[tvg_id] = filtered

    return {
        "window_start": window_start,
        "window_end":   window_end,
        "channels":     channel_list,
        "programs":     programs,
    }


@router.post("/match/", dependencies=_GUARDS)
async def match_epg(body: MatchRequest):
    client = DispatcharrClient()

    if body.source_ids:
        try:
            raw     = await client.get("/api/epg/sources/")
            sources = raw if isinstance(raw, list) else raw.get("results", [])
            sid_set = set(body.source_ids)
            source_url_map = {
                s["id"]: s.get("url", "")
                for s in sources
                if s.get("id") in sid_set and s.get("url")
            }
            if source_url_map:
                fire_warm_cache(source_url_map)
        except Exception as exc:
            logger.warning("[match] could not prefetch source URLs for XMLTV cache: %s", exc)

    results = await run_match(
        source_ids      = body.source_ids,
        channel_ids     = body.channel_ids,
        unassigned_only = body.unassigned_only,
        group_id        = body.group_id,
        tvg_id_filter   = body.tvg_id_filter,
        client          = client,
    )
    counts = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for r in results:
        counts[r["confidence"]] += 1
    return {"results": results, "total": len(results), "counts": counts}


@router.get("/search/", dependencies=_GUARDS)
async def search_epg_entries(
    source_ids: str = Query(""),
    q:          str = Query(""),
    limit:      int = Query(20, ge=1, le=100),
):
    if not q.strip():
        return []
    parsed_source_ids = [int(x) for x in source_ids.split(",") if x.strip().isdigit()]
    client = DispatcharrClient()
    return await search_epg(
        source_ids = parsed_source_ids,
        query      = q.strip(),
        limit      = limit,
        client     = client,
    )


@router.post("/now-playing/", dependencies=_GUARDS)
async def now_playing(body: NowPlayingRequest):
    tvg_id = body.tvg_id

    if tvg_id and body.source_ids:
        cached = get_now_playing(body.source_ids, tvg_id)
        if cached:
            return cached

    client = DispatcharrClient()
    now    = datetime.now(timezone.utc)

    if not tvg_id:
        try:
            entry  = await client.get(f"/api/epg/epgdata/{body.epg_data_id}/")
            tvg_id = entry.get("tvg_id") if isinstance(entry, dict) else None
        except Exception:
            pass

    if not tvg_id:
        return None

    for params in [
        {"tvg_id": tvg_id, "airing_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "fields": "title,start_time,end_time,description", "page_size": 1},
        {"tvg_id": tvg_id, "start_after": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "fields": "title,start_time,end_time,description", "page_size": 1},
    ]:
        try:
            raw      = await client.get("/api/epg/programs/search/", params=params)
            programs = raw if isinstance(raw, list) else raw.get("results", [])
            if programs:
                p    = programs[0]
                desc = (p.get("description") or "")[:300]
                result = {
                    "title":       p.get("title", ""),
                    "start":       p.get("start_time"),
                    "stop":        p.get("end_time"),
                    "description": desc,
                }
                if "start_after" in params:
                    result["upcoming"] = True
                return result
        except Exception:
            pass

    return None


@router.get("/cache-status/", dependencies=_GUARDS)
async def cache_status(source_ids: str = Query("")):
    parsed = [int(x) for x in source_ids.split(",") if x.strip().isdigit()]
    return _cache_status(parsed)


@router.get("/epg-warm-status/", dependencies=_GUARDS)
async def epg_warm_status():
    return _warm_status()


@router.post("/epg/refresh/", dependencies=_GUARDS)
async def epg_refresh():
    """Force re-warm all configured EPG sources immediately."""
    client = DispatcharrClient()
    try:
        raw     = await client.get("/api/epg/sources/")
        sources = raw if isinstance(raw, list) else raw.get("results", [])
        url_map = {s["id"]: s["url"] for s in sources if s.get("url")}
        if url_map:
            fire_warm_cache(url_map)
            return {"ok": True, "sources": len(url_map)}
        return {"ok": False, "message": "No EPG sources found"}
    except Exception as exc:
        raise HTTPException(502, detail=str(exc))


@router.get("/gn-station-db/status/", dependencies=_GUARDS)
async def gn_station_db_status():
    return _gn_db_status()


@router.post("/gn-station-db/update/", dependencies=_GUARDS)
async def gn_station_db_update():
    started = await _start_gn_db_update()
    return {"ok": True, "started": started}


@router.post("/epg/repull/", dependencies=_GUARDS)
async def epg_repull():
    """Clear XMLTV cache and force a fresh fetch of all configured EPG sources."""
    clear_xmltv_cache()
    client = DispatcharrClient()
    try:
        raw     = await client.get("/api/epg/sources/")
        sources = raw if isinstance(raw, list) else raw.get("results", [])
        url_map = {s["id"]: s["url"] for s in sources if s.get("url")}
        if url_map:
            fire_warm_cache(url_map)
            return {"ok": True, "sources": len(url_map)}
        return {"ok": False, "message": "No EPG sources found"}
    except Exception as exc:
        raise HTTPException(502, detail=str(exc))


@router.get("/logs/", dependencies=_GUARDS)
async def get_logs(limit: int = Query(200, ge=1, le=500)):
    entries = _log_buffer.get_logs()
    return {"entries": entries[-limit:]}


@router.delete("/channels/{channel_id}/", dependencies=_GUARDS)
async def delete_channel(channel_id: int):
    client = DispatcharrClient()
    try:
        await client.delete(f"/api/channels/channels/{channel_id}/")
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(502, detail=str(exc))


@router.post("/commit/", dependencies=_GUARDS)
async def commit_epg(body: CommitRequest):
    client = DispatcharrClient()
    result: dict = {}

    if body.associations:
        result = await client.post(
            "/api/channels/channels/batch-set-epg/",
            {
                "associations": [
                    {"channel_id": a.channel_id, "epg_data_id": a.epg_data_id}
                    for a in body.associations
                ]
            },
        )

    rename_errors: list[dict] = []
    for nc in body.name_changes:
        try:
            await client.patch(f"/api/channels/channels/{nc.channel_id}/", {"name": nc.name})
        except Exception as exc:
            logger.warning("[commit] rename failed for channel %d: %s", nc.channel_id, exc)
            rename_errors.append({"channel_id": nc.channel_id, "error": str(exc)})

    # Backfill GN station IDs from XMLTV cache and GN DB (opt-in via backfill_gn_id setting)
    backfill_count = 0
    s = get_epg_settings()
    if body.associations and s.get("backfill_gn_id"):
        try:
            channels_raw, all_epg = await asyncio.gather(
                fetch_channels(client),
                _fetch_all_epg_data(client),
            )
            channel_map = {c["id"]: c for c in channels_raw}
            epg_map     = {e["id"]: e for e in all_epg}
            patch_coros: list = []
            patch_ids:   list = []
            for assoc in body.associations:
                ch  = channel_map.get(assoc.channel_id)
                epg = epg_map.get(assoc.epg_data_id)
                if not ch or not epg:
                    continue
                # Skip if channel already has a station ID
                ch_tvc = (ch.get("effective_tvc_guide_stationid") or ch.get("tvc_guide_stationid") or "").strip()
                if ch_tvc:
                    continue
                # Look up from XMLTV <channel> elements we parsed during caching
                source_id  = epg.get("epg_source")
                tvg_id     = (epg.get("tvg_id") or "").strip()
                station_id = get_station_id(source_id, tvg_id) if source_id and tvg_id else None
                if not station_id and tvg_id:
                    station_id = lookup_gn_id(tvg_id)
                if station_id:
                    patch_coros.append(client.patch(
                        f"/api/channels/channels/{assoc.channel_id}/",
                        {"tvc_guide_stationid": station_id},
                    ))
                    patch_ids.append(assoc.channel_id)
            if patch_coros:
                patch_results = await asyncio.gather(*patch_coros, return_exceptions=True)
                for ch_id, res in zip(patch_ids, patch_results):
                    if isinstance(res, Exception):
                        logger.warning("[commit] gn_id backfill failed for ch %d: %s", ch_id, res)
                    else:
                        backfill_count += 1
                if backfill_count:
                    logger.info("[commit] gn_id backfill: %d channel(s) updated", backfill_count)
        except Exception as exc:
            logger.warning("[commit] gn_id backfill skipped: %s", exc)

    # Invalidate guide cache so next guide open reflects the new EPG assignments
    invalidate_guide_cache()

    if rename_errors:
        result = result if isinstance(result, dict) else {"detail": result}
        result["rename_errors"] = rename_errors
    if backfill_count:
        result = result if isinstance(result, dict) else {}
        result["gn_id_backfilled"] = backfill_count

    return result
