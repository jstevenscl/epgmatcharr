#!/usr/bin/env python3
"""
Build GN Station DB from Jesmann EPG sources.
Output: gn_station_db.sqlite

Sources:
  - epg.guru/7daygracenote/{Country}.xml.gz  (cable/satellite, 13 countries)
  - epg.jesmann.com/OTA/*.xml                (US OTA local markets)

The <channel id="..."> attribute in Jesmann's Gracenote XMLTVs is the
GN station ID. Call signs are extracted from <display-name> elements
and stored as the primary lookup key for EPGmatcharr's backfill feature.
"""

import gzip
import io
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

DB_PATH = Path("gn_station_db.sqlite")

GRACENOTE_SOURCES = [
    ("Australia",     "https://epg.guru/7daygracenote/Australia.xml.gz"),
    ("Canada",        "https://epg.guru/7daygracenote/Canada.xml.gz"),
    ("Finland",       "https://epg.guru/7daygracenote/Finland.xml.gz"),
    ("France",        "https://epg.guru/7daygracenote/France.xml.gz"),
    ("Germany",       "https://epg.guru/7daygracenote/Germany.xml.gz"),
    ("Italy",         "https://epg.guru/7daygracenote/Italy.xml.gz"),
    ("Netherlands",   "https://epg.guru/7daygracenote/Netherlands.xml.gz"),
    ("Norway",        "https://epg.guru/7daygracenote/Norway.xml.gz"),
    ("Spain",         "https://epg.guru/7daygracenote/Spain.xml.gz"),
    ("Sweden",        "https://epg.guru/7daygracenote/Sweden.xml.gz"),
    ("UnitedKingdom", "https://epg.guru/7daygracenote/UnitedKingdom.xml.gz"),
    ("UnitedStates",  "https://epg.guru/7daygracenote/UnitedStates.xml.gz"),
    ("USFast",        "https://epg.guru/7daygracenote/USFast.xml.gz"),
]

# Matches a clean call sign: all-caps alphanumeric/hyphen, 2-12 chars.
_CALLSIGN_RE = re.compile(r'^[A-Z0-9][A-Z0-9\-]{1,11}$')
_HTTP_HEADERS = {"User-Agent": "EPGmatcharr/1.0 (+https://github.com/jstevenscl/epgmatcharr)"}


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            station_id TEXT PRIMARY KEY,
            call_sign  TEXT NOT NULL,
            name       TEXT,
            icon_url   TEXT,
            source     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_callsign ON stations(call_sign);
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()


def _pick_callsign(names: list[str]) -> str:
    for n in names:
        if _CALLSIGN_RE.match(n):
            return n
    return names[0] if names else ""


def _parse_channels(data: bytes, is_gzipped: bool, source: str) -> list[tuple]:
    rows: list[tuple] = []
    try:
        raw = gzip.decompress(data) if is_gzipped else data
        for _, elem in ET.iterparse(io.BytesIO(raw), events=("end",)):
            if elem.tag == "channel":
                station_id = elem.get("id", "").strip()
                names = [n.text.strip() for n in elem.findall("display-name") if n.text and n.text.strip()]
                icon_el = elem.find("icon")
                icon_url = icon_el.get("src", "") if icon_el is not None else ""
                elem.clear()
                if not station_id or not names:
                    continue
                call_sign = _pick_callsign(names).upper()
                if call_sign:
                    rows.append((station_id, call_sign, names[0], icon_url, source))
            elif elem.tag == "programme":
                break
    except Exception as exc:
        print(f"  parse error [{source}]: {exc}", file=sys.stderr)
    return rows


def main() -> None:
    version = datetime.now(timezone.utc).strftime("v%Y.%m.%d")
    print(f"EPGmatcharr GN Station DB builder - {version}")

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    _init_db(conn)

    with httpx.Client(timeout=300.0, follow_redirects=True, headers=_HTTP_HEADERS) as client:

        # Phase 1: Gracenote country files
        print("\n-- Country EPG files --")
        gracenote_total = 0
        for country, url in GRACENOTE_SOURCES:
            print(f"  {country}...", end=" ", flush=True)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                rows = _parse_channels(resp.content, True, f"epg_guru_{country}")
                conn.executemany(
                    "INSERT OR IGNORE INTO stations(station_id,call_sign,name,icon_url,source) VALUES(?,?,?,?,?)",
                    rows,
                )
                conn.commit()
                gracenote_total += len(rows)
                print(f"{len(rows):,}")
            except Exception as exc:
                print(f"FAILED: {exc}", file=sys.stderr)

        print(f"  Subtotal: {gracenote_total:,} stations")

        # Phase 2: OTA market files
        print("\n-- OTA market files --")
        try:
            idx_resp = client.get("https://epg.jesmann.com/OTA/", timeout=30.0)
            idx_resp.raise_for_status()
            ota_urls = re.findall(
                r'href="(https://epg\.jesmann\.com/OTA/[^"]+\.xml)"', idx_resp.text
            )
            print(f"  Discovered {len(ota_urls)} market files")

            ota_new = 0
            for i, url in enumerate(ota_urls, 1):
                city = url.rsplit("/", 1)[-1].replace(".xml", "")
                try:
                    resp = client.get(url, timeout=120.0)
                    resp.raise_for_status()
                    rows = _parse_channels(resp.content, False, f"OTA_{city}")
                    conn.executemany(
                        "INSERT OR IGNORE INTO stations(station_id,call_sign,name,icon_url,source) VALUES(?,?,?,?,?)",
                        rows,
                    )
                    conn.commit()
                    ota_new += len(rows)
                except Exception as exc:
                    print(f"  FAILED {city}: {exc}", file=sys.stderr)

                if i % 25 == 0 or i == len(ota_urls):
                    print(f"  {i}/{len(ota_urls)} files - {ota_new:,} new stations")

        except Exception as exc:
            print(f"  OTA phase failed: {exc}", file=sys.stderr)

    final_count = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    conn.executemany(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        [
            ("version",  version),
            ("built_at", datetime.now(timezone.utc).isoformat()),
            ("count",    str(final_count)),
        ],
    )
    conn.commit()
    conn.close()

    size_kb = DB_PATH.stat().st_size // 1024
    print(f"\nDone: {final_count:,} stations, {size_kb:,} KB -> {DB_PATH}")


if __name__ == "__main__":
    main()
