"""
Builds fcc_market_db.sqlite: a call sign -> home market (city, state, zip) lookup,
used by Emby Sync to auto-derive which ZIP codes are needed for a user's channel
list, instead of requiring manual entry.

Sources (both public domain / permissively licensed, no scraping, no ToS risk):
  - FCC CDBS "facility" table: https://transition.fcc.gov/ftp/Bureaus/MB/Databases/cdbs/facility.zip
    US government work, 17 U.S.C. Sec 105 -- public domain.
  - GeoNames US postal code data: https://download.geonames.org/export/zip/US.zip
    CC BY 4.0.

Only covers over-the-air broadcast stations (these have FCC call signs). National
cable/streaming channels aren't in FCC data at all -- but they don't need ZIP-based
lookup anyway, since they're carried in ZIP-independent "None VMVPD" lineups.
"""

import collections
import io
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

FCC_FACILITY_URL = "https://transition.fcc.gov/ftp/Bureaus/MB/Databases/cdbs/facility.zip"
GEONAMES_US_URL  = "https://download.geonames.org/export/zip/US.zip"

# CDBS fac_service codes covering TV-band stations: full-power digital/analog TV,
# Class A (+ digital), low-power digital, TV translators.
TV_SERVICE_CODES = {"DT", "TV", "CA", "DC", "LD", "TX"}

# NOT under backend/data/ -- that maps to /app/data in the image, which is a
# persistent named volume (epgmatcharr_epgmatcharr_data). A volume mount shadows
# whatever was baked into the image at that path, so a file bundled there would be
# invisible at runtime. This is static reference data bundled with the app itself,
# not user data, so it belongs in the image layer, not the volume.
OUT_PATH = Path(__file__).parent.parent / "backend" / "fcc_market_db.sqlite"


_HEADERS = {"User-Agent": "EPGmatcharr/1.0 (+https://github.com/jstevenscl/epgmatcharr)"}


def _fetch(url: str) -> bytes:
    print(f"Fetching {url} ...")
    with httpx.Client(timeout=120.0, follow_redirects=True, headers=_HEADERS, verify=True) as client:
        resp = client.get(url, headers={"Accept": "*/*"})
        if resp.status_code == 403:
            # transition.fcc.gov 403s some httpx requests (redirect/header quirk) that
            # curl handles fine -- fall back to curl as a subprocess.
            import subprocess
            print("  httpx got 403, falling back to curl...")
            result = subprocess.run(["curl", "-sL", "-A", _HEADERS["User-Agent"], url], capture_output=True, timeout=120)
            if result.returncode == 0 and result.stdout:
                return result.stdout
        resp.raise_for_status()
        return resp.content


def _load_zip_crosswalk() -> dict[tuple[str, str], str]:
    """(CITY, STATE) -> representative ZIP, picking the dominant 3-digit prefix
    for that city to avoid non-geographic outlier zips (e.g. IRS-only zips that
    happen to be geocoded to a city's coordinates)."""
    raw = _fetch(GEONAMES_US_URL)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read("US.txt").decode("utf-8")

    city_zips: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        zip_code, place, state_abbr = parts[1], parts[2], parts[4]
        city_zips[(place.upper(), state_abbr.upper())].append(zip_code)

    crosswalk: dict[tuple[str, str], str] = {}
    for key, zips in city_zips.items():
        prefix_counts = collections.Counter(z[:3] for z in zips)
        dominant_prefix, _ = prefix_counts.most_common(1)[0]
        candidates = sorted(z for z in zips if z.startswith(dominant_prefix))
        crosswalk[key] = candidates[0]
    return crosswalk


def _load_fcc_stations() -> list[dict]:
    raw = _fetch(FCC_FACILITY_URL)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read("facility.dat").decode("latin-1")

    stations = []
    for line in text.splitlines():
        fields = line.split("|")
        if len(fields) < 20:
            continue
        comm_city, comm_state = fields[0].strip(), fields[1].strip()
        fac_callsign          = fields[5].strip()
        fac_service           = fields[10].strip()
        fac_status             = fields[16].strip()
        if fac_status != "LICEN" or fac_service not in TV_SERVICE_CODES:
            continue
        if not fac_callsign or fac_callsign == "NEW" or not comm_city or not comm_state:
            continue
        stations.append({
            "call_sign": fac_callsign.upper(),
            "city":      comm_city,
            "state":     comm_state,
        })
    return stations


def build() -> None:
    stations = _load_fcc_stations()
    print(f"Loaded {len(stations)} licensed TV-band stations from FCC data")

    crosswalk = _load_zip_crosswalk()
    print(f"Loaded {len(crosswalk)} city/state -> zip crosswalk entries")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if OUT_PATH.exists():
        OUT_PATH.unlink()

    conn = sqlite3.connect(str(OUT_PATH))
    conn.execute("""
        CREATE TABLE stations (
            call_sign TEXT PRIMARY KEY,
            city      TEXT,
            state     TEXT,
            zip_code  TEXT
        )
    """)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    resolved = 0
    unresolved_samples: list[str] = []
    rows = []
    for s in stations:
        zip_code = crosswalk.get((s["city"].upper(), s["state"].upper()))
        if zip_code:
            resolved += 1
        elif len(unresolved_samples) < 10:
            unresolved_samples.append(f"{s['call_sign']} ({s['city']}, {s['state']})")
        rows.append((s["call_sign"], s["city"], s["state"], zip_code))

    conn.executemany(
        "INSERT OR REPLACE INTO stations (call_sign, city, state, zip_code) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        ("built_at", datetime.now(timezone.utc).isoformat()),
    )
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("count", str(len(rows))))
    conn.commit()
    conn.close()

    print(f"Resolved zip for {resolved}/{len(rows)} stations")
    if unresolved_samples:
        print("Sample unresolved (city/state not found in zip crosswalk):", unresolved_samples)
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
