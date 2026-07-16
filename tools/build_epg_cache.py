#!/usr/bin/env python3
"""
Build a pre-parsed cache of the largest epg.guru XMLTV sources.

These 4 markets (both the "Standard (GN)" and "7day IPTV" tiers) are large
enough — 140-341MB compressed, 10-27M XML elements — that parsing them
in-process inside EPGmatcharr's own web server starves the GIL badly enough
to freeze the whole app for minutes at a time. Parsing them here instead,
in an isolated, disposable CI runner on a schedule, means that cost never
touches a live user-facing process. EPGmatcharr just downloads this small
SQLite file and does an indexed query.

One gzip-compressed SQLite file is published per (market, tier) — not one
combined file — so a user with only one or two of these sources configured
downloads only the small file(s) they actually need, not a bundle covering
markets they don't use. Output filenames: epg_guru_cache_{market}_{tier}.sqlite.gz
"""

import gzip
import io
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

OUTPUT_DIR = Path(".")

_MARKETS = ["Canada", "USFast", "UnitedStates", "UnitedStates-Locals"]
_TIERS   = ["7daygracenote", "7dayiptv"]

EPG_GURU_SOURCES = [
    (market, tier, f"https://epg.guru/{tier}/{market}.xml.gz")
    for market in _MARKETS
    for tier in _TIERS
]


def asset_name(market: str, tier: str) -> str:
    return f"epg_guru_cache_{market}_{tier}.sqlite.gz"


_HTTP_HEADERS = {"User-Agent": "EPGmatcharr/1.0 (+https://github.com/jstevenscl/epgmatcharr)"}

_BATCH_SIZE = 5000


def _open_xmltv(content: bytes):
    """Return a readable file-like for XMLTV content.

    httpx transparently decompresses Content-Encoding: gzip whenever we send
    Accept-Encoding: gzip (which it does by default), so `content` here is
    already plain bytes in that case — trust magic bytes only, not the URL's
    .gz suffix, which is not a reliable signal once httpx has already decoded
    the body. See backend/epg_cache.py's _open_xmltv for the full story.
    """
    if content[:2] == b"\x1f\x8b":
        return gzip.open(io.BytesIO(content))
    return io.BytesIO(content)


def _parse_and_store(conn, source_url: str, content: bytes, start_bound: str, end_bound: str) -> tuple[int, int]:
    """Parse one XMLTV source, batch-inserting rows into SQLite as it goes.

    Only programmes overlapping [start_bound, end_bound) are kept — these are
    XMLTV-format strings ("YYYYMMDDHHMMSS +0000"), comparable directly as
    plain strings since epg.guru consistently uses +0000 for every timestamp,
    avoiding a datetime parse on every one of the ~15M rows in the full set.
    An unfiltered build produced a 5.4GB SQLite file; the whole point of this
    cache is to be small and fast to fetch, so keeping the full 7-day span
    defeated the purpose. This runs every 4h, so the window doesn't need to
    be huge — just wide enough to comfortably cover realistic user window
    settings between refreshes.
    """
    fileobj = _open_xmltv(content)
    programme_rows: list[tuple] = []
    channel_rows:   list[tuple] = []
    n_programmes = 0
    n_channels   = 0

    def flush():
        if programme_rows:
            conn.executemany(
                "INSERT INTO programmes(source_url,tvg_id,start_utc,stop_utc,title,description) "
                "VALUES(?,?,?,?,?,?)",
                programme_rows,
            )
            programme_rows.clear()
        if channel_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO channels(source_url,tvg_id,tvc_guide_stationid) VALUES(?,?,?)",
                channel_rows,
            )
            channel_rows.clear()

    try:
        context = iter(ET.iterparse(fileobj, events=("start", "end")))
        try:
            _, root = next(context)
        except StopIteration:
            root = None

        for event, elem in context:
            if event != "end":
                continue

            if elem.tag == "channel":
                tvg_id = elem.get("id", "").strip()
                tvc_el = elem.find("tvc-guide-stationid")
                if tvg_id and tvc_el is not None and tvc_el.text:
                    channel_rows.append((source_url, tvg_id, tvc_el.text.strip()))
                    n_channels += 1
            elif elem.tag == "programme":
                tvg_id = elem.get("channel", "").strip()
                start  = elem.get("start", "").strip()
                stop   = elem.get("stop", "").strip()
                if tvg_id and start and stop and stop > start_bound and start < end_bound:
                    # title/desc are children of THIS programme element, already
                    # fully populated at this point — their own "end" events fired
                    # earlier, but we never clear a child on its own end (see the
                    # `continue` below), specifically so this read stays valid.
                    title_el = elem.find("title")
                    desc_el  = elem.find("desc")
                    programme_rows.append((
                        source_url,
                        tvg_id,
                        start,
                        stop,
                        (title_el.text or "") if title_el is not None else "",
                        ((desc_el.text or "") if desc_el is not None else "")[:300],
                    ))
                    n_programmes += 1
            else:
                # A child of an in-progress <programme>/<channel> (title, desc,
                # category, etc.). Do NOT clear it here — that would wipe its
                # .text before the parent's own "end" handler above reads it.
                # The parent's own elem.clear() below removes all of its
                # children once the parent itself closes, so nothing leaks.
                continue

            elem.clear()
            if root is not None:
                root.clear()

            if len(programme_rows) >= _BATCH_SIZE or len(channel_rows) >= _BATCH_SIZE:
                flush()
                conn.commit()
    except ET.ParseError as exc:
        print(f"  XML parse error: {exc}", file=sys.stderr)

    flush()
    conn.commit()
    return n_channels, n_programmes


def _init_db(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS programmes (
            source_url  TEXT NOT NULL,
            tvg_id      TEXT NOT NULL,
            start_utc   TEXT NOT NULL,
            stop_utc    TEXT NOT NULL,
            title       TEXT,
            description TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_programmes_lookup
            ON programmes(source_url, tvg_id, start_utc);

        CREATE TABLE IF NOT EXISTS channels (
            source_url          TEXT NOT NULL,
            tvg_id              TEXT NOT NULL,
            tvc_guide_stationid TEXT,
            PRIMARY KEY (source_url, tvg_id)
        );

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()


_WINDOW_HOURS_BEFORE = 6
_WINDOW_HOURS_AFTER  = 24


def main() -> None:
    import sqlite3
    from datetime import timedelta

    now     = datetime.now(timezone.utc)
    version = now.strftime("v%Y.%m.%d-%H%M")
    print(f"EPGmatcharr epg.guru cache builder - {version}")

    start_bound = (now - timedelta(hours=_WINDOW_HOURS_BEFORE)).strftime("%Y%m%d%H%M%S +0000")
    end_bound   = (now + timedelta(hours=_WINDOW_HOURS_AFTER)).strftime("%Y%m%d%H%M%S +0000")
    print(f"Window: -{_WINDOW_HOURS_BEFORE}h to +{_WINDOW_HOURS_AFTER}h ({start_bound} .. {end_bound})")

    grand_total_channels   = 0
    grand_total_programmes = 0
    built_files: list[str] = []

    with httpx.Client(timeout=180.0, follow_redirects=True, headers=_HTTP_HEADERS) as client:
        for market, tier, url in EPG_GURU_SOURCES:
            print(f"  {market} / {tier}...", end=" ", flush=True)
            raw_path = OUTPUT_DIR / f"epg_guru_cache_{market}_{tier}.sqlite"
            gz_path  = OUTPUT_DIR / asset_name(market, tier)
            if raw_path.exists():
                raw_path.unlink()

            conn = sqlite3.connect(str(raw_path))
            _init_db(conn)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                n_ch, n_prog = _parse_and_store(conn, url, resp.content, start_bound, end_bound)
                conn.executemany(
                    "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                    [
                        ("version",    version),
                        ("built_at",   datetime.now(timezone.utc).isoformat()),
                        ("market",     market),
                        ("tier",       tier),
                        ("source_url", url),
                        ("channels",   str(n_ch)),
                        ("programmes", str(n_prog)),
                    ],
                )
                conn.commit()
                conn.close()

                with open(raw_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=9) as f_out:
                    shutil.copyfileobj(f_in, f_out)
                raw_path.unlink()

                grand_total_channels   += n_ch
                grand_total_programmes += n_prog
                built_files.append(gz_path.name)

                gz_mb = gz_path.stat().st_size / (1024 * 1024)
                print(f"{n_ch:,} channels, {n_prog:,} programmes, {gz_mb:.1f} MB compressed")
            except Exception as exc:
                conn.close()
                print(f"FAILED: {exc}", file=sys.stderr)

    total_gz_mb = sum((OUTPUT_DIR / f).stat().st_size for f in built_files) / (1024 * 1024)
    print(f"\nDone: {grand_total_channels:,} channels, {grand_total_programmes:,} programmes, "
          f"{len(built_files)}/{len(EPG_GURU_SOURCES)} files built, {total_gz_mb:.1f} MB total compressed")

    assert grand_total_programmes > 0, "No programmes were parsed — something is wrong upstream."
    assert len(built_files) == len(EPG_GURU_SOURCES), "Not all sources built successfully."


if __name__ == "__main__":
    main()
