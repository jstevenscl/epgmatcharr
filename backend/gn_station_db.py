"""
GN Station DB — local cache of GN station ID → call sign mappings.

Built weekly from Jesmann EPG sources via the gn-station-db workflow in
the EPGmatcharr GitHub repo. The SQLite file is published as a pre-release
artifact tagged gn-db-{date} and downloaded on demand via Settings.

Used during EPG commit to backfill GN station IDs onto Dispatcharr channels
that don't already have tvc_guide_stationid set.
"""

import asyncio
import difflib
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

import httpx
from config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH       = DATA_DIR / "gn_station_db.sqlite"
_RELEASES_API = "https://api.github.com/repos/jstevenscl/epgmatcharr/releases?per_page=20"
_ASSET_NAME   = "gn_station_db.sqlite"
_HTTP_HEADERS = {"User-Agent": "EPGmatcharr/1.0 (+https://github.com/jstevenscl/epgmatcharr)"}

_PAREN_RE      = re.compile(r'\(([^)]+)\)')
_EXT_RE        = re.compile(r'\.[a-z]{2,3}$', re.IGNORECASE)
_EMBEDDED_CS   = re.compile(r'[KWkw][A-Za-z]{2,4}$')   # K/W callsign at tail of network-prefixed IDs

_STATUS: dict = {"updating": False, "progress": "", "error": None}


# ── Status ────────────────────────────────────────────────────────────────────

def get_status() -> dict:
    meta: dict = {"count": 0, "built_at": None, "version": None, "available": False}
    if DB_PATH.exists():
        try:
            conn  = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            count = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
            rows  = conn.execute("SELECT key, value FROM meta").fetchall()
            conn.close()
            m = dict(rows)
            meta.update({
                "count":     count,
                "built_at":  m.get("built_at"),
                "version":   m.get("version"),
                "available": count > 0,
            })
        except Exception:
            pass
    return {**_STATUS, **meta}


# ── Lookup ────────────────────────────────────────────────────────────────────

def _add_candidate(candidates: list[str], token: str) -> None:
    """Append token, inserting DT-suffixed forms ahead of a bare call-sign-shaped
    token so digital-tuner entries (the modern Gracenote convention, e.g. WKBWDT)
    outrank legacy bare entries (WKBW) that share the same call sign."""
    if _GN_CALLSIGN_RE.match(token):
        for variant in (f"{token}DT", f"{token}-DT"):
            if variant not in candidates:
                candidates.append(variant)
    if token not in candidates:
        candidates.append(token)


def _build_candidates(tvg_id: str) -> list[str]:
    """Extract ordered call sign candidates from a tvg_id string (no DB access)."""
    candidates: list[str] = []

    last_paren = re.search(r'\(([^)]+)\)\.[a-z]{2,3}$', tvg_id)
    if last_paren:
        _add_candidate(candidates, last_paren.group(1).upper())

    for m in _PAREN_RE.finditer(tvg_id):
        _add_candidate(candidates, m.group(1).upper())

    pre = _EXT_RE.sub('', re.sub(r'\(.*', '', tvg_id)).strip()
    if pre:
        _add_candidate(candidates, pre.upper())
        no_hyphen = pre.replace('-', '').upper()
        if no_hyphen != pre.upper():
            _add_candidate(candidates, no_hyphen)
        m2 = _EMBEDDED_CS.search(pre)
        if m2:
            _add_candidate(candidates, m2.group(0).upper())

    exact = _EXT_RE.sub('', tvg_id).upper()
    if exact not in candidates:
        candidates.append(exact)

    return candidates


def lookup_gn_id(tvg_id: str) -> Optional[str]:
    """
    Return the GN station ID for a tvg_id string, or None if not found.

    Handles:
      - Numeric tvg_id (already IS the station ID): "33585" -> "33585"
      - Jesmann IPTV format: "KVUE-DT(ABC)(KVUEDT).us" -> looks up "KVUEDT"
      - Plain call sign: "KVUEDT" -> direct lookup
    """
    if not tvg_id or not DB_PATH.exists():
        return None

    tvg_id = tvg_id.strip()

    if tvg_id.isdigit():
        return tvg_id

    candidates = _build_candidates(tvg_id)
    if not candidates:
        return None

    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        ph   = ','.join('?' * len(candidates))
        row  = conn.execute(
            f"SELECT station_id FROM stations WHERE call_sign IN ({ph}) LIMIT 1",
            candidates,
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as exc:
        logger.debug("[gn_station_db] lookup error: %s", exc)
        return None


def lookup_station(station_id: str) -> Optional[dict]:
    """Return {station_id, call_sign, name, icon_url} for a known GN station_id."""
    if not station_id or not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        row  = conn.execute(
            "SELECT station_id, call_sign, name, icon_url FROM stations WHERE station_id = ? LIMIT 1",
            (station_id,),
        ).fetchone()
        conn.close()
        return {"station_id": row[0], "call_sign": row[1], "name": row[2], "icon_url": row[3]} if row else None
    except Exception:
        return None


def search_stations(query: str, limit: int = 20, country: str = "") -> list[dict]:
    """Search stations by call_sign or name. Returns up to limit results with metadata."""
    q = query.strip().upper()
    if not q or not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        country_clause = ""
        params: list = [f"{q}%", f"%{q}%", q, f"{q}%"]
        if country:
            c = country.upper()
            if c == "US":
                country_clause = "AND (source LIKE 'epg_guru_United%' OR source LIKE 'epg_guru_USFast%' OR source LIKE 'OTA_%')"
            else:
                rev = {v: k for k, v in _SOURCE_COUNTRY.items()}
                src = rev.get(c)
                if src:
                    country_clause = f"AND source = '{src}'"
        rows = conn.execute(
            f"""SELECT station_id, call_sign, name, icon_url, source FROM stations
               WHERE (call_sign LIKE ? OR UPPER(name) LIKE ?)
               {country_clause}
               ORDER BY CASE WHEN call_sign = ?     THEN 0
                             WHEN call_sign LIKE ?  THEN 1
                             ELSE 2 END
               LIMIT ?""",
            (*params, limit),
        ).fetchall()
        conn.close()
        return [{"station_id": r[0], "call_sign": r[1], "name": r[2], "icon_url": r[3],
                 "country": _source_to_country(r[4])} for r in rows]
    except Exception:
        return []


def get_countries() -> list[str]:
    """Return sorted list of country codes present in the DB."""
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        rows = conn.execute("SELECT DISTINCT source FROM stations WHERE source IS NOT NULL").fetchall()
        conn.close()
        codes = set()
        for (src,) in rows:
            c = _source_to_country(src)
            if c:
                codes.add(c)
        return sorted(codes)
    except Exception:
        return []


_GN_CALLSIGN_RE    = re.compile(r'^[KWkw][A-Za-z]{2,4}$')
_GN_CALLSIGN_SPLIT = re.compile(r'[\s\-_./|]+')

# Maps source column value → ISO country code shown in UI
_SOURCE_COUNTRY: dict[str, str] = {
    "epg_guru_Australia":     "AU",
    "epg_guru_Canada":        "CA",
    "epg_guru_Finland":       "FI",
    "epg_guru_France":        "FR",
    "epg_guru_Germany":       "DE",
    "epg_guru_Italy":         "IT",
    "epg_guru_Netherlands":   "NL",
    "epg_guru_Norway":        "NO",
    "epg_guru_Spain":         "ES",
    "epg_guru_Sweden":        "SE",
    "epg_guru_UnitedKingdom": "GB",
    "epg_guru_UnitedStates":  "US",
    "epg_guru_USFast":        "US",
}


def _source_to_country(source: Optional[str]) -> str:
    if not source:
        return ""
    if source.startswith("OTA_"):
        return "US"
    return _SOURCE_COUNTRY.get(source, "")


def _extract_callsign_gn(text: str) -> Optional[str]:
    for token in _GN_CALLSIGN_SPLIT.split(text):
        if _GN_CALLSIGN_RE.match(token):
            return token.upper()
    return None


def _gn_confidence(score: float) -> str:
    if score >= 0.90: return "high"
    if score >= 0.65: return "medium"
    if score >= 0.30: return "low"
    return "none"


def _ch_logo(ch: dict) -> Optional[str]:
    for field in ("logo_url", "logo", "icon_url", "icon"):
        val = ch.get(field)
        if val:
            return val
    return None


def _match_gn_sync(channels: list[dict], limit: int = 5, recheck_existing: bool = False) -> dict:
    """Score and rank GN station candidates for each channel. Run in asyncio.to_thread.

    recheck_existing=True switches to audit mode: channels without an existing GN ID
    are skipped entirely, and the Tier-1 auto-1.0 override for channels that already
    have one is disabled so Tiers 2-5 compete honestly. Only channels where the honest
    top candidate is both high-confidence and different from what's currently stored
    are returned — surfacing stale matches (e.g. a bare call sign where a DT-suffixed
    entry now exists) without dragging every already-matched channel back into review.
    """
    summary: dict = {"high": 0, "medium": 0, "low": 0, "none": 0, "has_gn": 0}
    results: list[dict] = []

    if not DB_PATH.exists():
        return {"channels": [], "summary": summary}

    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    try:
        for ch in channels:
            tvg_id   = (ch.get("effective_tvg_id")              or ch.get("tvg_id")              or "").strip()
            existing = (ch.get("effective_tvc_guide_stationid") or ch.get("tvc_guide_stationid") or "").strip()
            name     = (ch.get("effective_name")                or ch.get("name")                or "").strip()

            if recheck_existing and not existing:
                continue

            candidates: list[dict] = []
            seen_ids: set = set()

            def _add(sid: str, cs: str, sname: str, icon: Optional[str], score: float, tier: str, source: str = "") -> None:
                if sid in seen_ids:
                    return
                seen_ids.add(sid)
                candidates.append({"station_id": sid, "call_sign": cs, "name": sname,
                                    "icon_url": icon, "score": round(score, 3), "tier": tier,
                                    "country": _source_to_country(source)})

            # Tier 1 — channel already has a GN ID (skipped in recheck mode so Tiers 2-5
            # compete honestly instead of being pre-empted by whatever is already stored)
            if existing and not recheck_existing:
                row = conn.execute(
                    "SELECT call_sign, name, icon_url, source FROM stations WHERE station_id = ? LIMIT 1",
                    (existing,),
                ).fetchone()
                cs, sn, ic, src = (row[0], row[1], row[2], row[3]) if row else (existing, existing, None, "")
                _add(existing, cs, sn, ic, 1.0, "existing", src or "")

            # Tier 2 — numeric tvg_id is the station ID directly
            if tvg_id and tvg_id.isdigit():
                row = conn.execute(
                    "SELECT station_id, call_sign, name, icon_url, source FROM stations WHERE station_id = ? LIMIT 1",
                    (tvg_id,),
                ).fetchone()
                if row:
                    _add(row[0], row[1], row[2], row[3], 0.95, "numeric_tvg_id", row[4] or "")

            # Tier 3 — callsign candidates extracted from tvg_id string (Jesmann IPTV format)
            if tvg_id and not tvg_id.isdigit():
                cands = _build_candidates(tvg_id)
                if cands:
                    ph   = ','.join('?' * len(cands))
                    rows = conn.execute(
                        f"SELECT station_id, call_sign, name, icon_url, source FROM stations WHERE call_sign IN ({ph})",
                        cands,
                    ).fetchall()
                    by_cs = {r[1].upper(): r for r in rows}
                    for i, c in enumerate(cands):
                        r = by_cs.get(c.upper())
                        if r:
                            _add(r[0], r[1], r[2], r[3], max(0.95 - i * 0.03, 0.75), "tvg_id_lookup", r[4] or "")

            # Tier 4 — callsign extracted from channel name → prefix search in GN DB
            if not candidates or candidates[0]["score"] < 0.90:
                ch_cs = _extract_callsign_gn(name) or _extract_callsign_gn(tvg_id)
                if ch_cs:
                    cs_dt_hyphen = f"{ch_cs}-DT"
                    cs_dt_plain  = f"{ch_cs}DT"
                    rows = conn.execute(
                        """SELECT station_id, call_sign, name, icon_url, source FROM stations
                           WHERE call_sign LIKE ?
                           ORDER BY CASE WHEN call_sign = ? THEN 1
                                         WHEN call_sign = ? THEN 1
                                         WHEN call_sign = ? THEN 2
                                         ELSE 3 END
                           LIMIT 15""",
                        (f"{ch_cs}%", cs_dt_hyphen, cs_dt_plain, ch_cs),
                    ).fetchall()
                    for row in rows:
                        cs_u = row[1].upper()
                        if cs_u in (cs_dt_hyphen, cs_dt_plain):
                            score = 0.88
                        elif cs_u == ch_cs:
                            score = 0.85
                        else:
                            score = 0.68
                        _add(row[0], row[1], row[2], row[3], score, "callsign", row[4] or "")

            # Tier 5 — fuzzy name match against station names
            if not candidates or candidates[0]["score"] < 0.65:
                q_upper = name[:20].upper()
                rows = conn.execute(
                    """SELECT station_id, call_sign, name, icon_url, source FROM stations
                       WHERE call_sign LIKE ? OR UPPER(name) LIKE ?
                       LIMIT 30""",
                    (f"{q_upper[:5]}%", f"%{q_upper[:12]}%"),
                ).fetchall()
                for row in rows:
                    ratio = difflib.SequenceMatcher(None, name.lower(), row[2].lower()).ratio()
                    if ratio >= 0.35:
                        _add(row[0], row[1], row[2], row[3], round(ratio * 0.80, 3), "name_fuzzy", row[4] or "")

            candidates.sort(key=lambda x: -x["score"])
            candidates = candidates[:limit]

            top_score = candidates[0]["score"] if candidates else 0.0

            if recheck_existing:
                # Only surface a channel if the honest top pick is high-confidence AND
                # actually differs from what's currently stored — otherwise skip it so
                # the review list stays limited to real, fixable staleness.
                if not candidates or candidates[0]["station_id"] == existing or _gn_confidence(top_score) != "high":
                    continue
                conf = "high"
                summary["high"] += 1
            elif existing:
                conf = "has_gn"
                summary["has_gn"] += 1
            else:
                conf = _gn_confidence(top_score)
                summary[conf] += 1

            results.append({
                "channel_id":          ch.get("id"),
                "name":                name,
                "tvg_id":              tvg_id or None,
                "channel_group_id":    ch.get("channel_group_id"),
                "channel_logo":        _ch_logo(ch),
                "tvc_guide_stationid": existing or None,
                "candidates":          candidates,
                "top_score":           top_score,
                "confidence":          conf,
            })
    finally:
        conn.close()

    _order = {"high": 0, "medium": 1, "low": 2, "none": 3, "has_gn": 4}
    results.sort(key=lambda x: (_order.get(x["confidence"], 5), x["name"].lower()))
    return {"channels": results, "summary": summary}


def _build_report_sync(channels: list[dict]) -> dict:
    """Build GN status report for all channels. Intended to run in asyncio.to_thread."""
    summary: dict = {"has_gn": 0, "can_fill": 0, "no_match": 0, "no_tvg_id": 0}
    result: list[dict] = []

    if not DB_PATH.exists():
        for ch in channels:
            result.append({
                "channel_id": ch.get("id"), "name": ch.get("effective_name") or ch.get("name", ""),
                "tvg_id": None, "channel_logo": _ch_logo(ch), "tvc_guide_stationid": None,
                "gn_call_sign": None, "gn_name": None, "gn_icon_url": None,
                "status": "no_tvg_id", "would_fill": None,
            })
        return {"channels": result, "summary": summary}

    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    try:
        for ch in channels:
            tvg_id   = (ch.get("effective_tvg_id")         or ch.get("tvg_id")         or "").strip()
            existing = (ch.get("effective_tvc_guide_stationid") or ch.get("tvc_guide_stationid") or "").strip()
            name     = (ch.get("effective_name")            or ch.get("name")           or "").strip()

            entry: dict = {
                "channel_id": ch.get("id"), "name": name, "tvg_id": tvg_id or None,
                "channel_group_id": ch.get("channel_group_id"),
                "channel_logo": _ch_logo(ch), "tvc_guide_stationid": existing or None,
                "gn_call_sign": None, "gn_name": None, "gn_icon_url": None,
                "status": None, "would_fill": None,
            }

            if existing:
                row = conn.execute(
                    "SELECT call_sign, name, icon_url FROM stations WHERE station_id = ? LIMIT 1",
                    (existing,),
                ).fetchone()
                if row:
                    entry["gn_call_sign"] = row[0]
                    entry["gn_name"]      = row[1]
                    entry["gn_icon_url"]  = row[2]
                entry["status"] = "has_gn"
                summary["has_gn"] += 1

            elif not tvg_id:
                entry["status"] = "no_tvg_id"
                summary["no_tvg_id"] += 1

            else:
                sid: Optional[str] = tvg_id if tvg_id.isdigit() else None
                if sid is None:
                    cands = _build_candidates(tvg_id)
                    if cands:
                        ph   = ','.join('?' * len(cands))
                        row2 = conn.execute(
                            f"SELECT station_id FROM stations WHERE call_sign IN ({ph}) LIMIT 1",
                            cands,
                        ).fetchone()
                        sid = row2[0] if row2 else None

                if sid:
                    row3 = conn.execute(
                        "SELECT call_sign, name, icon_url FROM stations WHERE station_id = ? LIMIT 1",
                        (sid,),
                    ).fetchone()
                    entry["would_fill"] = sid
                    if row3:
                        entry["gn_call_sign"] = row3[0]
                        entry["gn_name"]      = row3[1]
                        entry["gn_icon_url"]  = row3[2]
                    entry["status"] = "can_fill"
                    summary["can_fill"] += 1
                else:
                    entry["status"] = "no_match"
                    summary["no_match"] += 1

            result.append(entry)
    finally:
        conn.close()

    return {"channels": result, "summary": summary}


# ── Download from GitHub Releases ─────────────────────────────────────────────

async def _do_update() -> None:
    global _STATUS
    try:
        _STATUS.update({"updating": True, "progress": "Checking latest release…", "error": None})

        async with httpx.AsyncClient(
            timeout=30.0, headers=_HTTP_HEADERS, follow_redirects=True
        ) as client:
            resp = await client.get(_RELEASES_API)
            if resp.status_code != 200:
                raise RuntimeError(f"GitHub API returned HTTP {resp.status_code}")

            releases = resp.json()
            asset    = None
            version  = None
            for release in releases:
                tag = release.get("tag_name", "")
                if not tag.startswith("gn-db-"):
                    continue
                a = next((a for a in release.get("assets", []) if a["name"] == _ASSET_NAME), None)
                if a:
                    asset   = a
                    version = tag
                    break

            if not asset:
                raise RuntimeError("No GN Station DB release found in EPGmatcharr releases")

            url     = asset["browser_download_url"]
            size_mb = asset.get("size", 0) // (1024 * 1024)
            _STATUS["progress"] = f"Downloading {version} ({size_mb} MB)…"
            logger.info("[gn_station_db] downloading %s", version)

            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = DB_PATH.with_suffix(".tmp")

            async with client.stream("GET", url, timeout=300.0) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        f.write(chunk)

            tmp.replace(DB_PATH)

        count = get_status().get("count", 0)
        _STATUS.update({
            "updating": False,
            "progress": f"Updated to {version} — {count:,} stations",
            "error":    None,
        })
        logger.info("[gn_station_db] updated to %s (%d stations)", version, count)

    except Exception as exc:
        _STATUS.update({"updating": False, "progress": "", "error": str(exc)})
        logger.warning("[gn_station_db] update failed: %s", exc)


async def start_update() -> bool:
    """Download latest GN Station DB from EPGmatcharr releases. Returns False if already running."""
    if _STATUS["updating"]:
        return False
    asyncio.create_task(_do_update())
    return True
