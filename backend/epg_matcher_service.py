"""
EPG Matcher Service

Tiered matching logic:
  Tier 1  — exact tvg_id match                             → score 1.0
  Tier 2a — GN exact (ch.tvc_stationid == epg.tvc)  → score 0.98
  Tier 2b — GN fwd   (ch.tvc_stationid == epg.tvg)  → score 0.95
  Tier 2c — GN rev   (ch.tvg_id == epg.tvc)         → score 0.93
  Tier 3  — callsign match (K/W callsigns)                  → score 0.92
  Tier 4  — fuzzy normalized name match                     → score 0.0–0.89

Confidence thresholds:
  high   ≥ 0.90
  medium ≥ 0.65
  low    ≥ 0.30
  none   < 0.30
"""

import asyncio
import difflib
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_NOISE_TOKENS = re.compile(
    r"\b(hd|fhd|uhd|4k|sd|east|west|channel|tv|network|plus)\b",
    re.IGNORECASE,
)
_NON_ALPHA  = re.compile(r"[^a-z0-9]")
_WHITESPACE = re.compile(r"\s+")

_CALLSIGN_RE    = re.compile(r'^[KWkw][A-Za-z]{2,4}$')
_CALLSIGN_SPLIT = re.compile(r'[\s\-_./|]+')

CONF_HIGH   = 0.90
CONF_MEDIUM = 0.65
CONF_LOW    = 0.30

MAX_CANDIDATES = 8
FUZZY_CUTOFF   = 0.50


def _extract_callsign(text: str) -> Optional[str]:
    for token in _CALLSIGN_SPLIT.split(text):
        if _CALLSIGN_RE.match(token):
            return token.upper()
    return None


def _tvg_callsign(tvg_id: str) -> Optional[str]:
    if not tvg_id:
        return None
    base = _CALLSIGN_SPLIT.split(tvg_id)[0]
    return base.upper() if _CALLSIGN_RE.match(base) else None


def normalize_name(name: str) -> str:
    n = name.lower()
    n = _NOISE_TOKENS.sub(" ", n)
    n = _NON_ALPHA.sub(" ", n)
    return _WHITESPACE.sub(" ", n).strip()


def _confidence(score: float) -> str:
    if score >= CONF_HIGH:   return "high"
    if score >= CONF_MEDIUM: return "medium"
    if score >= CONF_LOW:    return "low"
    return "none"


async def fetch_epg_data(client) -> list[dict]:
    raw = await client.get("/api/epg/epgdata/")
    return raw if isinstance(raw, list) else raw.get("results", [])


async def fetch_channels(client) -> list[dict]:
    channels = []
    page = 1
    while True:
        resp = await client.get(
            "/api/channels/channels/",
            params={"page": page, "page_size": 500},
        )
        if isinstance(resp, list):
            channels.extend(resp)
            break
        results = resp.get("results", [])
        channels.extend(results)
        if not resp.get("next"):
            break
        page += 1
    return channels


def _compute_match(
    filtered_epg: list[dict],
    all_channels: list[dict],
    channel_ids: Optional[list[int]],
    unassigned_only: bool,
    group_id: Optional[int],
) -> list[dict]:
    epg_by_tvg_id:    dict[str, dict]         = {}
    epg_by_tvc_id:    dict[str, dict]         = {}  # keyed by tvc_guide_stationid
    epg_by_norm_name: dict[str, list[dict]]   = {}
    epg_by_callsign:  dict[str, list[dict]]   = {}
    _cs_seen:         dict[str, set]           = {}

    for e in filtered_epg:
        tvg = (e.get("tvg_id") or "").strip()
        if tvg and tvg not in epg_by_tvg_id:
            epg_by_tvg_id[tvg] = e
        tvc = (e.get("tvc_guide_stationid") or "").strip()
        if tvc and tvc not in epg_by_tvc_id:
            epg_by_tvc_id[tvc] = e
        norm = normalize_name(e.get("name", ""))
        if norm:
            epg_by_norm_name.setdefault(norm, []).append(e)
        eid = e.get("id")
        for cs in filter(None, {_tvg_callsign(tvg), _extract_callsign(e.get("name") or "")}):
            if eid not in _cs_seen.get(cs, set()):
                epg_by_callsign.setdefault(cs, []).append(e)
                _cs_seen.setdefault(cs, set()).add(eid)

    norm_epg_names = list(epg_by_norm_name.keys())

    channels = all_channels
    if channel_ids is not None:
        ch_id_set = set(channel_ids)
        channels = [c for c in channels if c.get("id") in ch_id_set]
    if group_id is not None:
        channels = [c for c in channels if c.get("channel_group_id") == group_id]
    if unassigned_only:
        channels = [c for c in channels if not c.get("epg_data_id")]

    logger.info("[epg_matcher] matching %d channels against %d EPG entries", len(channels), len(filtered_epg))

    results = []
    for ch in channels:
        ch_id   = ch.get("id")
        ch_name = (ch.get("effective_name") or ch.get("name") or "").strip()
        ch_tvg  = (ch.get("effective_tvg_id") or ch.get("tvg_id") or "").strip()
        ch_tvc  = (ch.get("effective_tvc_guide_stationid") or ch.get("tvc_guide_stationid") or "").strip()

        candidates: list[dict] = []
        seen_ids:   set[int]   = set()

        def _add(e: dict, score: float, tier: str) -> None:
            eid = e.get("id")
            if eid in seen_ids:
                return
            seen_ids.add(eid)
            candidates.append({
                "epg_data_id": eid,
                "name":        e.get("name", ""),
                "tvg_id":      e.get("tvg_id"),
                "icon_url":    e.get("icon_url"),
                "score":       round(score, 3),
                "tier":        tier,
                "epg_source_id": e.get("epg_source"),
            })

        # Tier 1: exact tvg_id
        if ch_tvg and ch_tvg in epg_by_tvg_id:
            _add(epg_by_tvg_id[ch_tvg], 1.0, "tvg_id_exact")
        # Tier 2a: GN exact — both sides have tvc_guide_stationid
        if ch_tvc and ch_tvc in epg_by_tvc_id:
            _add(epg_by_tvc_id[ch_tvc], 0.98, "gn_exact")
        # Tier 2b: GN fwd — channel tvc matches EPG tvg_id (jesmanns guide format)
        if ch_tvc and ch_tvc in epg_by_tvg_id:
            _add(epg_by_tvg_id[ch_tvc], 0.95, "gn_id")
        # Tier 2c: GN rev — channel tvg_id matches EPG tvc_guide_stationid
        if ch_tvg and ch_tvg in epg_by_tvc_id:
            _add(epg_by_tvc_id[ch_tvg], 0.93, "gn_rev")
        if not candidates or candidates[0]["score"] < CONF_HIGH:
            ch_cs = _extract_callsign(ch_name) or _tvg_callsign(ch_tvg)
            if ch_cs and ch_cs in epg_by_callsign:
                for e in epg_by_callsign[ch_cs]:
                    _add(e, 0.92, "callsign")
        if not candidates or candidates[0]["score"] < CONF_HIGH:
            norm_ch = normalize_name(ch_name)
            if norm_ch and norm_epg_names:
                for cn in difflib.get_close_matches(norm_ch, norm_epg_names, n=10, cutoff=FUZZY_CUTOFF):
                    ratio = difflib.SequenceMatcher(None, norm_ch, cn).ratio()
                    for e in epg_by_norm_name[cn]:
                        _add(e, ratio, "name_fuzzy")

        candidates.sort(key=lambda x: x["score"], reverse=True)
        candidates = candidates[:MAX_CANDIDATES]

        top_score = candidates[0]["score"] if candidates else 0.0
        results.append({
            "channel_id":           ch_id,
            "channel_name":         ch_name,
            "channel_number":       ch.get("channel_number"),
            "channel_uuid":         ch.get("uuid"),
            "channel_tvg_id":       ch_tvg or None,
            "tvc_guide_stationid":  ch_tvc or None,
            "current_epg_data_id":  ch.get("epg_data_id"),
            "channel_group_id":     ch.get("channel_group_id"),
            "confidence":           _confidence(top_score),
            "top_score":            top_score,
            "candidates":           candidates,
        })

    _order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    results.sort(key=lambda x: (_order[x["confidence"]], x["channel_name"].lower()))
    return results


async def run_match(
    source_ids: list[int],
    channel_ids: Optional[list[int]],
    unassigned_only: bool,
    group_id: Optional[int],
    client,
    tvg_id_filter: Optional[str] = None,
) -> list[dict]:
    all_epg = await fetch_epg_data(client)
    source_set = set(source_ids) if source_ids else None
    filtered_epg = [
        e for e in all_epg
        if source_set is None or e.get("epg_source") in source_set
    ]
    if tvg_id_filter:
        f = tvg_id_filter.lower()
        filtered_epg = [e for e in filtered_epg if f in (e.get("tvg_id") or "").lower()]

    logger.info("[epg_matcher] %d total EPG entries, %d after filter", len(all_epg), len(filtered_epg))

    all_channels = await fetch_channels(client)
    return await asyncio.to_thread(
        _compute_match,
        filtered_epg, all_channels, channel_ids, unassigned_only, group_id,
    )


async def search_epg(
    source_ids: list[int],
    query: str,
    limit: int,
    client,
) -> list[dict]:
    all_epg = await fetch_epg_data(client)
    source_set = set(source_ids) if source_ids else None
    filtered = [e for e in all_epg if source_set is None or e.get("epg_source") in source_set]

    q_lower    = query.lower()
    q_norm     = normalize_name(query)
    q_callsign = _extract_callsign(query) or _tvg_callsign(query)

    scored = []
    for e in filtered:
        name = (e.get("name") or "").lower()
        tvg  = (e.get("tvg_id") or "").lower()
        if q_callsign:
            cs = _tvg_callsign(e.get("tvg_id") or "") or _extract_callsign(e.get("name") or "")
            if cs == q_callsign:
                scored.append((0.95, e))
                continue
        if q_lower in name or q_lower in tvg:
            score = 1.0 if name == q_lower or tvg == q_lower else 0.85
        else:
            norm  = normalize_name(e.get("name", ""))
            score = difflib.SequenceMatcher(None, q_norm, norm).ratio()
            if score < 0.45:
                continue
        scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "epg_data_id":   e.get("id"),
            "name":          e.get("name", ""),
            "tvg_id":        e.get("tvg_id"),
            "icon_url":      e.get("icon_url"),
            "score":         round(s, 3),
            "epg_source_id": e.get("epg_source"),
        }
        for s, e in scored[:limit]
    ]
