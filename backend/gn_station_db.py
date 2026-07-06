"""
GN Station DB — local cache of GN station ID → call sign mappings.

Built weekly from Jesmann EPG sources via the gn-station-db workflow in
the EPGmatcharr GitHub repo. The SQLite file is published as a pre-release
artifact tagged gn-db-{date} and downloaded on demand via Settings.

Used during EPG commit to backfill GN station IDs onto Dispatcharr channels
that don't already have tvc_guide_stationid set.
"""

import asyncio
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

def _build_candidates(tvg_id: str) -> list[str]:
    """Extract ordered call sign candidates from a tvg_id string (no DB access)."""
    candidates: list[str] = []

    last_paren = re.search(r'\(([^)]+)\)\.[a-z]{2,3}$', tvg_id)
    if last_paren:
        candidates.append(last_paren.group(1).upper())

    for m in _PAREN_RE.finditer(tvg_id):
        c = m.group(1).upper()
        if c not in candidates:
            candidates.append(c)

    pre = _EXT_RE.sub('', re.sub(r'\(.*', '', tvg_id)).strip()
    if pre:
        candidates.append(pre.upper())
        no_hyphen = pre.replace('-', '').upper()
        if no_hyphen != pre.upper():
            candidates.append(no_hyphen)
        m2 = _EMBEDDED_CS.search(pre)
        if m2:
            cs = m2.group(0).upper()
            if cs not in candidates:
                candidates.append(cs)

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


def search_stations(query: str, limit: int = 20) -> list[dict]:
    """Search stations by call_sign or name. Returns up to limit results with metadata."""
    q = query.strip().upper()
    if not q or not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        rows = conn.execute(
            """SELECT station_id, call_sign, name, icon_url FROM stations
               WHERE call_sign LIKE ? OR UPPER(name) LIKE ?
               ORDER BY CASE WHEN call_sign = ?     THEN 0
                             WHEN call_sign LIKE ?  THEN 1
                             ELSE 2 END
               LIMIT ?""",
            (f"{q}%", f"%{q}%", q, f"{q}%", limit),
        ).fetchall()
        conn.close()
        return [{"station_id": r[0], "call_sign": r[1], "name": r[2], "icon_url": r[3]} for r in rows]
    except Exception:
        return []


def _ch_logo(ch: dict) -> Optional[str]:
    for field in ("logo_url", "logo", "icon_url", "icon"):
        val = ch.get(field)
        if val:
            return val
    return None


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
