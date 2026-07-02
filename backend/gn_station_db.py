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

_PAREN_RE = re.compile(r'\(([^)]+)\)')

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

    # Already a numeric station ID
    if tvg_id.isdigit():
        return tvg_id

    candidates: list[str] = []

    # Last paren group before dot-extension: "KVUE-DT(ABC)(KVUEDT).us" -> "KVUEDT"
    last_paren = re.search(r'\(([^)]+)\)\.[a-z]{2,3}$', tvg_id)
    if last_paren:
        candidates.append(last_paren.group(1).upper())

    # All paren groups
    for m in _PAREN_RE.finditer(tvg_id):
        c = m.group(1).upper()
        if c not in candidates:
            candidates.append(c)

    # Part before first paren, with and without hyphens
    pre = re.sub(r'\(.*', '', tvg_id).strip()
    if pre:
        candidates.append(pre.upper())
        no_hyphen = pre.replace('-', '').upper()
        if no_hyphen != pre.upper():
            candidates.append(no_hyphen)

    # Exact tvg_id as-is
    candidates.append(tvg_id.upper())

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
