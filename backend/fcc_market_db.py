"""
Call sign -> home market (city, state, ZIP) lookup, built from public FCC station
license data (see tools/build_fcc_market_db.py). Bundled with the app -- this is
static reference data, not user data, so it lives in the image layer rather than
the /app/data volume.

Used by Emby Sync to auto-derive which ZIP codes are needed for a user's channel
list instead of requiring manual entry: extract each channel's call sign, look up
its market here, and feed the resulting ZIPs into the Gracenote lineup discovery
that already exists.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "fcc_market_db.sqlite"


def is_available() -> bool:
    return DB_PATH.exists()


def lookup_zip(call_sign: str) -> Optional[str]:
    """Returns the representative ZIP for a call sign's home market, trying
    progressively shorter prefixes (e.g. "WKBW-DT" -> "WKBW-DT" -> "WKBW") since
    FCC call signs don't carry the DT/CD/LD digital-suffix convention GN station
    data uses."""
    if not call_sign or not DB_PATH.exists():
        return None
    candidates = _candidates(call_sign.strip().upper())
    if not candidates:
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        ph   = ",".join("?" * len(candidates))
        row  = conn.execute(
            f"SELECT zip_code FROM stations WHERE call_sign IN ({ph}) AND zip_code IS NOT NULL LIMIT 1",
            candidates,
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as exc:
        logger.debug("[fcc_market_db] lookup error: %s", exc)
        return None


_FCC_SUFFIXES = ("-TV", "-DT", "-LD", "-CD", "-CA")


def _candidates(call_sign: str) -> list[str]:
    """FCC's own call sign format is inconsistent per-station: some are bare
    (KVUE), some hyphen-suffixed (WKBW-TV). The input here (from GN Matcher /
    channel name extraction) is typically bare (WKBW) or has the no-hyphen GN
    DT/CD/LD suffix convention (WKBWDT) -- neither of which is guaranteed to be
    how any given station is stored in the FCC data. First reduce to a base call
    sign, then try both bare and every plausible FCC-style suffixed form."""
    base = call_sign
    for suffix in _FCC_SUFFIXES + tuple(s.lstrip("-") for s in _FCC_SUFFIXES):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = base.split("-")[0]

    out = [call_sign, base]
    for suffix in _FCC_SUFFIXES:
        variant = f"{base}{suffix}"
        if variant not in out:
            out.append(variant)
    return out


def get_status() -> dict:
    if not DB_PATH.exists():
        return {"available": False, "count": 0}
    try:
        conn  = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        count = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
        meta  = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        conn.close()
        return {"available": True, "count": count, "built_at": meta.get("built_at")}
    except Exception:
        return {"available": False, "count": 0}
