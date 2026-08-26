"""
Unit tests for routes._compute_backfill_plan -- the shared logic behind both
POST /api/backfill-preview/ and the backfill step of POST /api/commit/. See
epgmatcharr-zlc: a force-overwrite option (with a preview beforehand) for
channels that already have a GN ID / tvg-id, since normal backfill only ever
fills in an empty field.
"""

import asyncio

import routes
from routes import EpgAssociation


class FakeClient:
    pass


CHANNELS = [
    {"id": 1, "name": "Empty GN Channel",   "tvc_guide_stationid": "",      "tvg_id": ""},
    {"id": 2, "name": "Already Mapped",     "tvc_guide_stationid": "OLD1",  "tvg_id": "old.tvg"},
    {"id": 3, "name": "Already Correct",    "tvc_guide_stationid": "12345", "tvg_id": "abc.tvg"},
]
EPG_DATA = [
    {"id": 100, "tvg_id": "abc.tvg", "epg_source": 1},
]

ASSOCIATIONS = [
    EpgAssociation(channel_id=1, epg_data_id=100),
    EpgAssociation(channel_id=2, epg_data_id=100),
    EpgAssociation(channel_id=3, epg_data_id=100),
]


def _patch_lookups(monkeypatch):
    monkeypatch.setattr(routes, "fetch_channels", lambda client: _async(CHANNELS))
    monkeypatch.setattr(routes, "_fetch_all_epg_data", lambda client: _async(EPG_DATA))
    # source_id=1 + tvg_id "abc.tvg" resolves to station "12345" via GN Station DB.
    monkeypatch.setattr(routes, "get_station_id", lambda source_id, tvg_id: "12345" if tvg_id == "abc.tvg" else None)
    monkeypatch.setattr(routes, "lookup_gn_id", lambda tvg_id: None)


async def _async(value):
    return value


def test_non_forced_backfill_only_fills_empty_fields(monkeypatch):
    _patch_lookups(monkeypatch)

    plan = asyncio.run(routes._compute_backfill_plan(
        FakeClient(), ASSOCIATIONS, do_tvc=True, do_tvg=True, force_tvc=False, force_tvg=False,
    ))

    by_id = {entry["channel_id"]: entry for entry in plan}
    assert set(by_id) == {1}
    assert by_id[1]["fields"]["tvc_guide_stationid"] == {"old": None, "new": "12345"}
    assert by_id[1]["fields"]["tvg_id"] == {"old": None, "new": "abc.tvg"}


def test_force_overwrites_existing_values_but_skips_already_correct(monkeypatch):
    _patch_lookups(monkeypatch)

    plan = asyncio.run(routes._compute_backfill_plan(
        FakeClient(), ASSOCIATIONS, do_tvc=True, do_tvg=True, force_tvc=True, force_tvg=True,
    ))

    by_id = {entry["channel_id"]: entry for entry in plan}
    # Channel 1: empty -> filled, same as non-forced.
    assert by_id[1]["fields"]["tvc_guide_stationid"] == {"old": None, "new": "12345"}
    # Channel 2: already has a (wrong) value -- force overwrites it.
    assert by_id[2]["fields"]["tvc_guide_stationid"] == {"old": "OLD1", "new": "12345"}
    assert by_id[2]["fields"]["tvg_id"] == {"old": "old.tvg", "new": "abc.tvg"}
    # Channel 3 already holds the value backfill would produce -- no-op, not
    # included in the plan even with force on (nothing would actually change).
    assert 3 not in by_id


def test_no_plan_when_neither_field_enabled(monkeypatch):
    _patch_lookups(monkeypatch)

    plan = asyncio.run(routes._compute_backfill_plan(
        FakeClient(), ASSOCIATIONS, do_tvc=False, do_tvg=False,
    ))

    assert plan == []
