"""
Regression test for the GN Station DB US-callsign misattribution fix.

epg.guru's per-country files are carriage listings, not station-origin
listings, so a real US station carried on another country's cable/satellite
system can get scraped into that country's file and win the station_id race
in tools/build_gn_db.py's INSERT OR IGNORE. Confirmed live: KMSP-DT
(Minneapolis, a real US Fox affiliate) stored with source='epg_guru_Canada',
which made it fail GN Matcher's "US" country filter and disappear from
search entirely, while its own correctly-tagged subchannels (KMSP-DT2/DT3/...)
still showed up -- see gn_station_db._is_us_callsign / _source_to_country.
"""

import sqlite3

import gn_station_db as g


def _make_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE stations (
            station_id TEXT PRIMARY KEY,
            call_sign  TEXT NOT NULL,
            name       TEXT,
            icon_url   TEXT,
            source     TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.executemany(
        "INSERT INTO stations(station_id, call_sign, name, icon_url, source) VALUES (?,?,?,?,?)",
        [
            # Misattributed: real US station, but scraped into Canada's file first.
            ("24504", "KMSP-DT", "KMSP-DT", "", "epg_guru_Canada"),
            # Correctly-attributed US subchannels of the same station.
            ("46092", "KMSP-DT2", "KMSP-DT2", "", "epg_guru_USFast"),
            ("106987", "KMSP-DT3", "KMSP-DT3", "", "epg_guru_UnitedStates"),
            # A real Canadian station -- must NOT be reclassified.
            ("99001", "CFTO-DT", "CFTO-DT", "", "epg_guru_Canada"),
        ],
    )
    conn.commit()
    conn.close()


def test_us_callsign_shows_under_us_filter_despite_wrong_source(tmp_path, monkeypatch):
    db_path = tmp_path / "gn_station_db.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(g, "DB_PATH", db_path)

    results = {r["call_sign"]: r for r in g.search_stations("kmsp", country="US")}

    assert "KMSP-DT" in results
    assert results["KMSP-DT"]["country"] == "US"
    assert "KMSP-DT2" in results and "KMSP-DT3" in results


def test_us_callsign_excluded_from_the_wrong_countrys_filter(tmp_path, monkeypatch):
    db_path = tmp_path / "gn_station_db.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(g, "DB_PATH", db_path)

    results = {r["call_sign"]: r for r in g.search_stations("kmsp", country="CA")}
    assert "KMSP-DT" not in results


def test_real_canadian_station_is_unaffected(tmp_path, monkeypatch):
    db_path = tmp_path / "gn_station_db.sqlite"
    _make_db(db_path)
    monkeypatch.setattr(g, "DB_PATH", db_path)

    results = {r["call_sign"]: r for r in g.search_stations("cfto", country="CA")}
    assert results["CFTO-DT"]["country"] == "CA"

    # And it must not leak into a US-filtered search either.
    us_results = {r["call_sign"]: r for r in g.search_stations("cfto", country="US")}
    assert "CFTO-DT" not in us_results


def test_is_us_callsign_matches_digital_suffixed_forms_only():
    assert g._is_us_callsign("KMSP-DT")
    assert g._is_us_callsign("KMSP")
    assert g._is_us_callsign("wcbs")  # case-insensitive
    assert not g._is_us_callsign("CFTO-DT")   # Canadian prefix
    assert not g._is_us_callsign("WEATH")     # too long to be a real base callsign
    assert not g._is_us_callsign(None)
