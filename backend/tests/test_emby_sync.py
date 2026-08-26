"""
Exercises the real emby_sync.py logic against the mock Emby server (see
mock_emby_server.py), with Dispatcharr / GN Station DB / FCC market DB faked
out so these tests don't need any of those services running either.

The main scenario (_seed_kids_scenario) reproduces the reported bug: a user
excludes every channel group except a cable-only one ("Kids" -- Nick Jr,
Disney Junior, etc., none of which have an FCC OTA call sign), and Emby Sync
should still find lineup coverage for it using a ZIP derived from the
excluded OTA group. See epgmatcharr-6n4.
"""

import asyncio

import fcc_market_db
import emby_sync

NEWS_GROUP_ID = 1
KIDS_GROUP_ID = 2

CHANNELS = [
    {"name": "WKBW",          "tvc_guide_stationid": "12345", "channel_number": 2.0,  "channel_group_id": NEWS_GROUP_ID},
    {"name": "Nick Jr",       "tvc_guide_stationid": "99001", "channel_number": 50.0, "channel_group_id": KIDS_GROUP_ID},
    {"name": "Disney Junior", "tvc_guide_stationid": "99002", "channel_number": 51.0, "channel_group_id": KIDS_GROUP_ID},
]
GROUPS = [{"id": NEWS_GROUP_ID, "name": "News"}, {"id": KIDS_GROUP_ID, "name": "Kids"}]

# Only the OTA (News) station has a resolvable call sign -- Kids channels are
# cable-only networks, which is the whole point of the scenario.
CALL_SIGNS = {"12345": "WKBW-DT"}
MARKET_ZIP = "14202"
NATIONWIDE_ZIP = "20500"

LOCAL_CABLE_LINEUP = "USA-CABLE-14202"
NATIONWIDE_LINEUP  = "USA-DITV-X"


class FakeDispatcharrClient:
    def __init__(self, channels, groups):
        self._channels = channels
        self._groups = groups

    async def get(self, path, params=None):
        if path == "/api/channels/channels/":
            return self._channels
        if path == "/api/channels/groups/":
            return self._groups
        raise AssertionError(f"unexpected Dispatcharr path {path}")


async def _fake_fetch_channels(client):
    return client._channels


def _emby_channel(chno: str, name: str) -> dict:
    return {
        "Id": f"item-{chno}", "Name": name, "ChannelNumber": chno,
        "ManagementId": f"tuner1_hdhr_{chno}", "ListingsChannelId": None,
    }


def _seed_kids_scenario(mock_emby) -> None:
    mock_emby.state["tuner_hosts"] = [
        {"Id": "tuner1", "FriendlyName": "HD Homerun", "Type": "hdhomerun", "AllowMappingByNumber": True},
    ]
    mock_emby.state["channels"] = [
        _emby_channel("2", "WKBW"), _emby_channel("50", "Nick Jr"), _emby_channel("51", "Disney Junior"),
    ]
    mock_emby.state["lineups"] = {
        (MARKET_ZIP, "US"):     [{"Id": LOCAL_CABLE_LINEUP, "Name": "Local Cable Co"}],
        (NATIONWIDE_ZIP, "US"): [{"Id": NATIONWIDE_LINEUP, "Name": "DIRECTV"}],
    }
    mock_emby.state["lineup_stations"] = {
        # Local cable carries all three -- this is what a fix should find.
        LOCAL_CABLE_LINEUP: [
            {"Id": "12345", "Name": "WKBW"}, {"Id": "99001", "Name": "Nick Jr"}, {"Id": "99002", "Name": "Disney Junior"},
        ],
        # Nationwide/satellite carries the OTA affiliate but NOT the cable-only
        # Kids networks -- this is what the bug's fallback would have found.
        NATIONWIDE_LINEUP: [{"Id": "12345", "Name": "WKBW"}],
    }


def _dispatcharr_env(monkeypatch):
    client = FakeDispatcharrClient(CHANNELS, GROUPS)
    monkeypatch.setattr(emby_sync, "DispatcharrClient", lambda: client)
    monkeypatch.setattr(emby_sync, "fetch_channels", _fake_fetch_channels)
    monkeypatch.setattr(emby_sync, "get_emby_excluded_groups", lambda: [NEWS_GROUP_ID])
    monkeypatch.setattr(emby_sync, "lookup_station", lambda sid: ({"call_sign": CALL_SIGNS[sid]} if sid in CALL_SIGNS else None))
    monkeypatch.setattr(fcc_market_db, "is_available", lambda: True)
    monkeypatch.setattr(fcc_market_db, "lookup_zip", lambda call_sign: MARKET_ZIP if call_sign in CALL_SIGNS.values() else None)
    return client


def test_excluding_all_but_a_cable_only_group_still_finds_coverage(emby_env, monkeypatch):
    """Regression test for epgmatcharr-6n4: excluding every group except a
    cable-only one must not starve ZIP auto-derivation. It should still use
    the News channel's market ZIP (even though News itself is excluded) to
    find local cable coverage for the remaining Kids channels, instead of
    falling back to nationwide-only lineups that don't carry them."""
    _dispatcharr_env(monkeypatch)
    _seed_kids_scenario(emby_env)

    report = asyncio.run(emby_sync.preview_coverage())

    assert report["nationwide_fallback_used"] is False
    assert {p["zip_code"] for p in report["regions_searched"]} == {MARKET_ZIP}
    assert {item["name"] for item in report["would_map"]} == {"Nick Jr", "Disney Junior"}
    assert report["no_lineup_coverage"] == []


def test_push_maps_remaining_channels_and_leaves_excluded_group_alone(emby_env, monkeypatch):
    _dispatcharr_env(monkeypatch)
    _seed_kids_scenario(emby_env)
    # Pre-existing mapping on the excluded (News) channel -- must survive untouched.
    emby_env.state["channels"][0]["ListingsChannelId"] = "pre-existing-mapping"

    result = asyncio.run(emby_sync.push_mappings())

    assert result["mapped_count"] == 2
    assert result["failed"] == []
    by_number = {c["ChannelNumber"]: c for c in emby_env.state["channels"]}
    assert by_number["50"]["ListingsChannelId"] == "99001"
    assert by_number["51"]["ListingsChannelId"] == "99002"
    assert by_number["2"]["ListingsChannelId"] == "pre-existing-mapping"


def test_clear_all_guide_data_resets_managed_channels_but_skips_excluded_group(emby_env, monkeypatch):
    _dispatcharr_env(monkeypatch)
    _seed_kids_scenario(emby_env)
    for ch in emby_env.state["channels"]:
        ch["ListingsChannelId"] = "some-station"
    emby_env.state["providers"]["existing"] = {
        "Id": "existing", "Type": "embygn", "ListingsId": LOCAL_CABLE_LINEUP,
        "Country": "US", "ZipCode": MARKET_ZIP, "Name": "Local Cable Co",
    }

    result = asyncio.run(emby_sync.clear_all_guide_data())

    assert result["cleared_count"] == 2
    assert result["failed"] == []
    by_number = {c["ChannelNumber"]: c for c in emby_env.state["channels"]}
    assert by_number["50"]["ListingsChannelId"] is None
    assert by_number["51"]["ListingsChannelId"] is None
    assert by_number["2"]["ListingsChannelId"] == "some-station"
