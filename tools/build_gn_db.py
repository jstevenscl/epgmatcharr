#!/usr/bin/env python3
"""
Build GN Station DB from Jesmann EPG sources.
Output: gn_station_db.sqlite

Sources:
  - epg.guru/7daygracenote/{Country}.xml.gz  (cable/satellite, auto-discovered)
  - epg.jesmann.com/OTA/*.xml                (US OTA local markets)

The country list is scraped from epg.guru's own directory index (see
epg_guru_index.py) rather than hardcoded, so a country epg.guru adds later
is picked up on the next scheduled run with no code change -- same pattern
already used below for the OTA market-file phase.

The <channel id="..."> attribute in Jesmann's 7daygn XMLTVs is the
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

from epg_guru_index import discover_countries

DB_PATH = Path("gn_station_db.sqlite")

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


def _clear_ambiguous_callsigns(conn: sqlite3.Connection) -> int:
    """A call_sign shared by 2+ different stations can't disambiguate between
    them, so it's useless (worse than useless -- actively misleading) for
    callsign-based matching regardless of *why* it collides. In practice
    this is dominated by _pick_callsign() grabbing a network/brand name
    Gracenote lists as one of a station's alternate display-names instead of
    the station's own identifier (confirmed via epgmatcharr-dr3, e.g. 34
    different real ABC affiliates -- "American Broadcasting Company",
    "ZFBTV-Bermuda Broadcasting", "WBTS-CD", etc. -- all stored with
    call_sign="ABC"; "NINE" covers 126 different Australian Nine Network
    stations). Rather than trying to build a blocklist of known brand names
    across every country's conventions -- infeasible and never complete --
    detect the collision directly from the data itself and clear it, self-
    correcting as new stations/sources are added. The station's own row
    (station_id, name, icon_url) is untouched, so numeric-ID-based lookups
    are unaffected -- only the unreliable call_sign is cleared."""
    ambiguous = conn.execute(
        "SELECT call_sign, COUNT(*) FROM stations WHERE call_sign != '' GROUP BY call_sign HAVING COUNT(*) > 1"
    ).fetchall()
    if not ambiguous:
        return 0
    conn.executemany("UPDATE stations SET call_sign = '' WHERE call_sign = ?", [(cs,) for cs, _ in ambiguous])
    conn.commit()
    return sum(n for _, n in ambiguous)


def _pick_callsign(names: list[str]) -> str:
    for n in names:
        if _CALLSIGN_RE.match(n):
            return n
    return names[0] if names else ""


def _open_bytes(data: bytes) -> bytes:
    """epg.guru sources are served over gzip Content-Encoding (httpx
    transparently decompresses whenever it signals Accept-Encoding: gzip,
    which it does by default) or, via some CDN/proxy paths, already fully
    decompressed regardless of the .xml.gz URL suffix -- so `data` may or
    may not still be gzip-compressed by the time it gets here. Trust magic
    bytes only, never the URL suffix. See backend/epg_cache.py's
    _open_xmltv for the fuller story this mirrors.
    """
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def _parse_channels(data: bytes, source: str) -> list[tuple]:
    rows: list[tuple] = []
    try:
        raw = _open_bytes(data)
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

        # Phase 1: epg.guru country files (auto-discovered from the live
        # index -- see epg_guru_index.py -- instead of a hardcoded list, so
        # new countries epg.guru adds show up here with no code change)
        print("\n-- Country EPG files --")
        epg_guru_total = 0
        try:
            countries = discover_countries(client, "7daygracenote")
            print(f"  Discovered {len(countries)} country files")
        except Exception as exc:
            print(f"  Country discovery FAILED: {exc}", file=sys.stderr)
            countries = []

        for country, url in countries:
            print(f"  {country}...", end=" ", flush=True)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                rows = _parse_channels(resp.content, f"epg_guru_{country}")
                conn.executemany(
                    "INSERT OR IGNORE INTO stations(station_id,call_sign,name,icon_url,source) VALUES(?,?,?,?,?)",
                    rows,
                )
                conn.commit()
                epg_guru_total += len(rows)
                print(f"{len(rows):,}")
            except Exception as exc:
                print(f"FAILED: {exc}", file=sys.stderr)

        print(f"  Subtotal: {epg_guru_total:,} stations")

        # Phase 2: OTA market files
        print("\n-- OTA market files --")
        try:
            idx_resp = client.get("https://epg.jesmann.com/OTA/", timeout=30.0)
            idx_resp.raise_for_status()
            # The index lists relative hrefs (e.g. href="EurekaSpringsAR-OTA.xml"),
            # not absolute URLs -- matching only the absolute form found 0 entries
            # every run. Accept either: build the full URL for a bare filename,
            # use an already-absolute href as-is.
            ota_urls = [
                name if name.startswith("http") else f"https://epg.jesmann.com/OTA/{name}"
                for name in re.findall(r'href="([^"]+\.xml)"', idx_resp.text)
            ]
            print(f"  Discovered {len(ota_urls)} market files")

            ota_new = 0
            for i, url in enumerate(ota_urls, 1):
                city = url.rsplit("/", 1)[-1].replace(".xml", "")
                try:
                    resp = client.get(url, timeout=120.0)
                    resp.raise_for_status()
                    rows = _parse_channels(resp.content, f"OTA_{city}")
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

        cleared = _clear_ambiguous_callsigns(conn)
        print(f"\n-- Cleared call_sign on {cleared:,} stations (ambiguous -- shared with another station) --")

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
