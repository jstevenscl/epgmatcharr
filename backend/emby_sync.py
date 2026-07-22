"""
Emby embygn (Gracenote) guide integration.

Discovers which Gracenote lineups cover the user's channels (by ZIP), picks the
minimal covering set via greedy set-cover, and pushes explicit channel-to-station
mappings using the GN station IDs EPGmatcharr already knows from GN Matcher.

preview_coverage() is fully reversible: every trial ListingProvider it adds while
probing lineup coverage gets deleted again before it returns, regardless of outcome.
push_mappings() re-runs the same discovery, but keeps the winning lineups configured
on Emby and actually writes the channel mappings.

Channels are joined between Dispatcharr and Emby by CHANNEL NUMBER, not name.
Display names collide in practice (e.g. a SiriusXM audio channel and a TV channel
both literally named "CNN") -- channel number is the identifier both systems use
for tuning and is unique per channel, so it's the only safe join key.

Both entrypoints are scoped to the tuner(s) actually hosting channels this run
manages (see _active_tuner_ids) -- Emby's own APIs (get_managed_channels,
TunerHosts) are cross-tuner by default, and a naive read of them would let a
sync for one tuner's Gracenote lineup clear mappings or flip settings on a
completely unrelated tuner (e.g. one carrying dummy/Teamarr channels with no
GN ids of their own). See epgmatcharr-nh7.
"""

import asyncio
import logging

import fcc_market_db
from config import get_emby_excluded_groups
from dispatcharr_client import DispatcharrClient
from emby_client import EmbyClient
from epg_matcher_service import fetch_channels
from gn_station_db import lookup_station

# Emby indexes a newly-added ListingProvider's lineup data asynchronously --
# ChannelMappingOptions reliably returns 0 channels for a few seconds right after
# POST /ListingProviders, then populates. Poll instead of trusting the first read.
_INDEX_POLL_ATTEMPTS = 6
_INDEX_POLL_DELAY_S  = 2.0

# Emby also runs its own call-sign-based auto-match in the background after a
# provider becomes active, independent of explicit ChannelMappings calls. It
# settles within seconds rather than drifting continuously (observed empirically);
# push_mappings waits this long, then re-asserts its own choices as authoritative.
_SETTLE_DELAY_S = 8.0

logger = logging.getLogger(__name__)


def _tuner_id_for(ech: dict, known_tuner_ids: set[str]) -> str | None:
    """Recovers which TunerHost an Emby channel belongs to. Emby's ManagementId
    embeds the owning TunerHost's Id as a literal prefix -- confirmed empirically
    against a real two-tuner setup: ManagementId "{TunerHost.Id}_{type}_
    {ChannelNumber}", e.g. "47c3b556..._hdhr_200" for TunerHost Id "47c3b556...".
    Matching against the real, live tuner Ids (rather than guessing at the
    delimiter format between the id/type/number segments) is what makes this
    robust regardless of tuner type or naming."""
    mgmt_id = ech.get("ManagementId") or ""
    for tid in known_tuner_ids:
        if mgmt_id.startswith(tid):
            return tid
    return None


def _active_tuner_ids(station_map: dict, emby_by_number: dict, known_tuner_ids: set[str]) -> set[str]:
    """The tuner(s) that actually host at least one channel EPGmatcharr is
    managing this run -- i.e. what this sync is scoped to. Inferred automatically
    from which physical channels have a GN station id, rather than requiring a
    manual tuner picker (see epgmatcharr-0ne for that as a separate feature).
    Used to keep _clear_unknown and disable_auto_match_by_number from touching a
    tuner the user never asked to sync (epgmatcharr-nh7)."""
    ids = {_tuner_id_for(emby_by_number[chno], known_tuner_ids) for chno in station_map if chno in emby_by_number}
    ids.discard(None)
    return ids


def _normalize_channel_number(num) -> str | None:
    """Dispatcharr's channel_number is a float (e.g. 1012.0); Emby's ChannelNumber
    is a plain string (e.g. "1012" or "24.1" for a subchannel). Normalize both to
    the same string form so they compare equal."""
    if num is None or num == "":
        return None
    try:
        f = float(num)
    except (TypeError, ValueError):
        return str(num).strip() or None
    return str(int(f)) if f == int(f) else str(f)


async def _load_dispatcharr_channels() -> tuple[dict[str, dict], int]:
    """Returns ({channel_number: {name, station_id, group}} for channels with a
    GN id AND a channel number, total channel count).

    group is included because channel names collide across unrelated channels
    in practice -- e.g. a SiriusXM audio channel and a cable news channel both
    literally named "CNN" (also true of Fox News, MSNOW, and others). Channel
    number is still the actual join key with Emby; group is purely so the UI
    can show the user which "CNN" a given row actually is."""
    client   = DispatcharrClient()
    channels, groups = await asyncio.gather(fetch_channels(client), client.get("/api/channels/groups/"))
    group_list = groups if isinstance(groups, list) else groups.get("results", [])
    group_map: dict[int, str] = {g["id"]: g["name"] for g in group_list if g.get("id")}

    station_map: dict[str, dict] = {}
    for ch in channels:
        name = (ch.get("effective_name") or ch.get("name") or "").strip()
        sid  = (ch.get("effective_tvc_guide_stationid") or ch.get("tvc_guide_stationid") or "").strip()
        chno = _normalize_channel_number(ch.get("effective_channel_number") or ch.get("channel_number"))
        if chno and sid:
            station_map[chno] = {
                "name": name, "station_id": sid,
                "group": group_map.get(ch.get("channel_group_id"), ""),
                "group_id": ch.get("channel_group_id"),
            }
    return station_map, len(channels)


def _split_excluded(station_map: dict[str, dict]) -> tuple[dict[str, dict], set[str]]:
    """Removes channels in an Emby-Sync-excluded group from station_map. Separate
    from any GN Matcher-side settings -- a channel can have a perfectly correct
    GN station ID and still be something the user never wants pushed to Emby (e.g.
    SiriusXM audio channels that happen to share a name with a real TV channel).
    Returns (filtered_station_map, excluded_channel_numbers)."""
    excluded_group_ids = set(get_emby_excluded_groups())
    if not excluded_group_ids:
        return station_map, set()
    excluded_chnos = {chno for chno, info in station_map.items() if info.get("group_id") in excluded_group_ids}
    if not excluded_chnos:
        return station_map, set()
    return {chno: info for chno, info in station_map.items() if chno not in excluded_chnos}, excluded_chnos


def _auto_derive_zip_codes(station_map: dict[str, dict]) -> set[str]:
    """For every channel with a known GN station ID, look up its call sign (from
    the GN Station DB -- already correctly resolved, no re-parsing of noisy
    channel names needed) and then its home market ZIP (from the FCC market DB).
    This is what lets Emby Sync work without the user manually entering ZIPs for
    every market their channel list happens to span."""
    if not fcc_market_db.is_available():
        return set()
    zips: set[str] = set()
    seen_station_ids: set[str] = set()
    for info in station_map.values():
        sid = info["station_id"]
        if sid in seen_station_ids:
            continue
        seen_station_ids.add(sid)
        station = lookup_station(sid)
        if not station or not station.get("call_sign"):
            continue
        zip_code = fcc_market_db.lookup_zip(station["call_sign"])
        if zip_code:
            zips.add(zip_code)
    return zips


async def _discover_candidates(emby: EmbyClient, zip_codes: list[str], country: str) -> dict[str, dict]:
    """Dedup lineups across all configured ZIPs. {listings_id: {listings_id, name, zip_code}}."""
    candidates: dict[str, dict] = {}
    for zip_code in zip_codes:
        lineups = await emby.discover_lineups(zip_code, country)
        for lu in lineups:
            lid = lu["Id"]
            if lid not in candidates:
                candidates[lid] = {"listings_id": lid, "name": lu["Name"], "zip_code": zip_code}
    return candidates


async def _trial_one(emby: EmbyClient, cand: dict, country: str) -> tuple[str, dict] | None:
    try:
        # ListingProviders wants the 3-letter country form (e.g. "USA") in the request
        # body, while lineup discovery wants the 2-letter ISO form ("US") as a query
        # param. Derive the 3-letter form from the lineup ID's own prefix (all lineup
        # IDs are "{3-letter}-...") instead of the configured 2-letter country, so this
        # stays correct regardless of what's passed in.
        provider_country = cand["listings_id"].split("-", 1)[0] or country
        provider = await emby.add_provider(cand["listings_id"], cand["zip_code"], provider_country, f"[epgmatcharr-trial] {cand['name']}")
        pid      = provider["Id"]

        channels: list[dict] = []
        for attempt in range(_INDEX_POLL_ATTEMPTS):
            channels = await emby.get_channel_mapping_options(pid)
            if channels:
                break
            if attempt < _INDEX_POLL_ATTEMPTS - 1:
                await asyncio.sleep(_INDEX_POLL_DELAY_S)

        return cand["listings_id"], {"provider_id": pid, "stations": {c["Id"] for c in channels}}
    except Exception as exc:
        logger.warning("[emby_sync] failed to trial lineup %s (%s): %s", cand["listings_id"], cand["name"], exc)
        return None


async def _trial_coverage(emby: EmbyClient, candidates: dict[str, dict], country: str) -> dict[str, dict]:
    """Add each candidate lineup as a temp provider, fetch its station coverage.
    {listings_id: {provider_id, stations: set[str]}}. Runs concurrently."""
    results = await asyncio.gather(*(_trial_one(emby, cand, country) for cand in candidates.values()))
    return dict(r for r in results if r is not None)


def _greedy_select(coverage: dict[str, dict], needed: set[str]) -> list[str]:
    """Greedy set-cover. Returns listings_ids selected, in the order they were picked."""
    remaining = set(needed)
    selected: list[str] = []
    pool = dict(coverage)
    while remaining:
        best_lid, best_gain = None, 0
        for lid, info in pool.items():
            gain = len(info["stations"] & remaining)
            if gain > best_gain:
                best_lid, best_gain = lid, gain
        if best_lid is None:
            break
        selected.append(best_lid)
        remaining -= pool[best_lid]["stations"]
        del pool[best_lid]
    return selected


async def _cleanup(emby: EmbyClient, providers: list[str]) -> None:
    async def _del(pid: str):
        try:
            await emby.delete_provider(pid)
        except Exception as exc:
            logger.warning("[emby_sync] cleanup failed for provider %s: %s", pid, exc)
    await asyncio.gather(*(_del(pid) for pid in providers))


async def list_tuners() -> list[dict]:
    """Tuner hosts configured in Emby, for a UI picker (see tuner_id on
    preview_coverage/push_mappings). label prefers DeviceId (Dispatcharr's own
    output-profile name, e.g. "dispatcharr-hdhr-Emby-3") over FriendlyName --
    confirmed empirically that Emby reports the same generic FriendlyName
    ("HD Homerun") for every HDHomeRun-emulated tuner regardless of which
    underlying Dispatcharr profile it actually is, so FriendlyName alone can't
    tell two tuners apart in the UI."""
    emby  = EmbyClient()
    hosts = await emby.list_tuner_hosts()
    return [
        {
            "id":    t["Id"],
            "label": t.get("DeviceId") or t.get("FriendlyName") or t.get("Type") or t["Id"],
            "url":   t.get("Url", ""),
        }
        for t in hosts if t.get("Id")
    ]


async def preview_coverage(
    zip_codes: list[str] | None = None, country: str = "US", respect_existing: bool = False,
    tuner_id: str | None = None,
) -> dict:
    """Fully reversible dry run. Categorizes every EPGmatcharr channel with a known
    GN station ID into would_map / left_unchanged / no_emby_match / no_lineup_coverage,
    and reports the minimal lineup set that would need to be added to achieve that
    coverage.

    zip_codes is optional: ZIPs are auto-derived from each channel's already-known
    call sign via the FCC market DB. Any manually-supplied ZIPs are added on top --
    useful for markets the auto-derivation misses, or channels with no GN id yet.

    respect_existing, when True, mirrors push_mappings' behavior of leaving any
    channel that already has a *different* mapping in Emby untouched rather than
    overwriting it -- those channels are reported under left_unchanged instead of
    would_map so the preview accurately reflects what a push would actually do.

    tuner_id, when given, restricts everything to just that tuner's channels
    (see push_mappings for why) instead of auto-inferring scope from station_map."""
    station_map, total_channels = await _load_dispatcharr_channels()
    no_gn_id_count = total_channels - len(station_map)
    station_map, excluded_chnos = _split_excluded(station_map)

    emby = EmbyClient()
    emby_channels  = await emby.get_managed_channels()
    emby_by_number = {c["ChannelNumber"]: c for c in emby_channels if c.get("ChannelNumber")}
    known_tuner_ids = {t["Id"] for t in await emby.list_tuner_hosts() if t.get("Id")}

    if tuner_id:
        if tuner_id not in known_tuner_ids:
            raise ValueError(f"Unknown tuner_id {tuner_id!r}")
        station_map = {
            chno: info for chno, info in station_map.items()
            if chno in emby_by_number and _tuner_id_for(emby_by_number[chno], known_tuner_ids) == tuner_id
        }
    needed_ids = {v["station_id"] for v in station_map.values()}

    auto_zips  = _auto_derive_zip_codes(station_map)
    zip_codes  = sorted(auto_zips | set(zip_codes or []))
    if not zip_codes:
        raise ValueError("No ZIP codes could be auto-derived and none were provided")

    # Scope to only the tuner(s) actually hosting channels this run manages --
    # an explicit tuner_id wins outright; otherwise inferred from station_map
    # (see _active_tuner_ids). Plain reads above (get_managed_channels/
    # list_tuner_hosts) are safe before disable_auto_match_by_number(); no
    # provider is added or made active until _trial_coverage below, which is
    # the actual risk window that ordering guards against.
    active_tuner_ids = {tuner_id} if tuner_id else _active_tuner_ids(station_map, emby_by_number, known_tuner_ids)
    tuners_fixed      = await emby.disable_auto_match_by_number(active_tuner_ids or None)

    candidates = await _discover_candidates(emby, zip_codes, country)
    coverage   = await _trial_coverage(emby, candidates, country)
    selected   = _greedy_select(coverage, needed_ids)

    covered_ids: set[str] = set()
    for lid in selected:
        covered_ids |= coverage[lid]["stations"]

    # Preview is fully reversible -- delete every trial provider before returning.
    await _cleanup(emby, [info["provider_id"] for info in coverage.values()])

    would_map: list[dict] = []
    left_unchanged: list[dict] = []
    no_emby_match: list[dict] = []
    no_lineup_coverage: list[dict] = []
    for chno, info in station_map.items():
        name, sid, group = info["name"], info["station_id"], info.get("group", "")
        ech = emby_by_number.get(chno)
        if not ech:
            no_emby_match.append({"name": name, "station_id": sid, "channel_number": chno, "group": group})
            continue
        current = ech.get("ListingsChannelId")
        item = {"name": name, "station_id": sid, "channel_number": chno, "current_station_id": current, "group": group}
        if current == sid:
            # Already correctly mapped in Emby, whether by a prior push or a manual
            # override -- never a "no coverage" problem regardless of whether this
            # run's own lineup discovery happens to carry it.
            would_map.append(item)
        elif sid not in covered_ids:
            no_lineup_coverage.append(item)
        elif respect_existing and current:
            left_unchanged.append(item)
        else:
            would_map.append(item)

    return {
        "total_channels":      total_channels,
        "would_map":           would_map,
        "left_unchanged":      left_unchanged,
        "no_gn_id_count":      no_gn_id_count,
        "excluded_count":      len(excluded_chnos),
        "no_emby_match":       no_emby_match,
        "no_lineup_coverage":  no_lineup_coverage,
        "tuners_fixed":        tuners_fixed,
        "zip_codes_used":      zip_codes,
        "auto_derived_zip_count": len(auto_zips),
        "respect_existing":    respect_existing,
        "selected_lineups": [
            {"listings_id": lid, "name": candidates[lid]["name"], "zip_code": candidates[lid]["zip_code"],
             "channels_covered": len(coverage[lid]["stations"] & needed_ids)}
            for lid in selected
        ],
        "candidates_tried": len(candidates),
    }


async def push_mappings(
    zip_codes: list[str] | None = None, country: str = "US", respect_existing: bool = False,
    tuner_id: str | None = None,
) -> dict:
    """Re-runs discovery, but keeps the winning lineups configured on Emby and
    pushes an explicit ChannelMappings call for every channel that resolves.

    zip_codes is optional -- see preview_coverage.

    respect_existing, when True, leaves any channel that already has a *different*
    mapping in Emby untouched -- neither the initial push nor the settle-and-correct
    pass will overwrite it. Use this for setups where some channels are intentionally
    mapped by hand (or by Emby's own auto-match) and should not be managed by
    EPGmatcharr. Channels with no GN id in EPGmatcharr at all are unaffected by this
    flag -- clearing those (if mapped) is a separate, unconditional behavior; see
    _clear_unknown.

    tuner_id, when given, restricts the whole sync to just that tuner's channels
    (see list_tuners) -- e.g. push only the tuner carrying real Gracenote channels
    without touching a different tuner that also happens to have some GN-matched
    channels on it. When omitted, scope is auto-inferred from every channel with
    a GN id (the original, tuner-agnostic behavior)."""
    station_map, _ = await _load_dispatcharr_channels()
    station_map, excluded_chnos = _split_excluded(station_map)

    emby = EmbyClient()
    emby_channels  = await emby.get_managed_channels()
    emby_by_number = {c["ChannelNumber"]: c for c in emby_channels if c.get("ChannelNumber")}
    known_tuner_ids = {t["Id"] for t in await emby.list_tuner_hosts() if t.get("Id")}

    if tuner_id:
        if tuner_id not in known_tuner_ids:
            raise ValueError(f"Unknown tuner_id {tuner_id!r}")
        station_map = {
            chno: info for chno, info in station_map.items()
            if chno in emby_by_number and _tuner_id_for(emby_by_number[chno], known_tuner_ids) == tuner_id
        }
    needed_ids = {v["station_id"] for v in station_map.values()}

    auto_zips = _auto_derive_zip_codes(station_map)
    zip_codes = sorted(auto_zips | set(zip_codes or []))
    if not zip_codes:
        raise ValueError("No ZIP codes could be auto-derived and none were provided")

    # Scope to only the tuner(s) actually hosting channels this run manages --
    # an explicit tuner_id wins outright; otherwise inferred from station_map
    # (see _active_tuner_ids). A different tuner (e.g. one carrying dummy/
    # Teamarr channels with no GN id) must never be touched by anything below.
    active_tuner_ids = {tuner_id} if tuner_id else _active_tuner_ids(station_map, emby_by_number, known_tuner_ids)

    # Must happen before any provider is added/kept live: with AllowMappingByNumber
    # on, Emby auto-matches any unmapped channel to whatever the active provider
    # calls that same channel NUMBER -- a coincidence, not a station match -- the
    # moment a provider becomes active. That silently corrupts channels this code
    # deliberately chose not to map. (The reads above are safe before this --
    # no provider is added/active until _trial_coverage below.)
    tuners_fixed = await emby.disable_auto_match_by_number(active_tuner_ids or None)

    candidates = await _discover_candidates(emby, zip_codes, country)
    coverage   = await _trial_coverage(emby, candidates, country)
    selected   = _greedy_select(coverage, needed_ids)

    lineup_for_station: dict[str, str] = {}
    for lid in selected:
        info = coverage[lid]
        for sid in info["stations"]:
            lineup_for_station.setdefault(sid, info["provider_id"])

    winning_pids = {coverage[lid]["provider_id"] for lid in selected}
    losing_pids  = [info["provider_id"] for lid, info in coverage.items() if info["provider_id"] not in winning_pids]
    await _cleanup(emby, losing_pids)

    any_provider_id = next(iter(winning_pids), None)

    async def _map_one(chno: str, name: str, sid: str):
        ech = emby_by_number.get(chno)
        if not ech:
            return {"name": name, "station_id": sid, "status": "skipped", "reason": "not_found_in_emby"}
        current = ech.get("ListingsChannelId")
        if respect_existing and current and current != sid:
            return {"name": name, "station_id": sid, "status": "left_unchanged", "current_station_id": current}
        pid = lineup_for_station.get(sid)
        if not pid:
            # Not covered by any selected lineup. If this channel already has a
            # mapping (a stale push from an earlier run, or Emby's own prior
            # auto-match), clear it rather than silently leaving wrong data in
            # place -- "we chose not to map this" must mean "unmapped", not
            # "whatever happened to already be there."
            if ech.get("ListingsChannelId") and any_provider_id:
                try:
                    await emby.clear_channel_mapping(any_provider_id, ech["ManagementId"])
                    await emby.clear_channel_images(ech["Id"])
                    return {"name": name, "station_id": sid, "status": "cleared", "reason": "no_lineup_coverage"}
                except Exception as exc:
                    return {"name": name, "station_id": sid, "status": "failed", "error": str(exc)}
            return {"name": name, "station_id": sid, "status": "skipped", "reason": "no_lineup_coverage"}
        try:
            await emby.push_channel_mapping(pid, ech["ManagementId"], sid)
            # Emby caches artwork independently of the mapping -- a channel that
            # previously had a different (wrong) mapping can keep showing that
            # mapping's logo even after this push corrects the data. Force a
            # re-fetch so the displayed logo matches what's actually mapped now.
            await emby.clear_channel_images(ech["Id"])
            return {"name": name, "station_id": sid, "status": "mapped"}
        except Exception as exc:
            return {"name": name, "station_id": sid, "status": "failed", "error": str(exc)}

    results = await asyncio.gather(*(_map_one(chno, info["name"], info["station_id"]) for chno, info in station_map.items()))
    results_by_chno = dict(zip(station_map.keys(), results))
    preserved_chnos = {chno for chno, r in results_by_chno.items() if r["status"] == "left_unchanged"}

    # Emby runs its own independent call-sign-based auto-match in the background
    # once a provider is active -- separate from AllowMappingByNumber, and not
    # something we found a setting to disable. It can overwrite an explicit push
    # moments later with a different (often stale/duplicate) entry that happens
    # to share the same call sign text. It settles quickly rather than drifting
    # continuously, so wait it out once, then re-assert our explicit choices as
    # authoritative over whatever Emby decided on its own.
    await asyncio.sleep(_SETTLE_DELAY_S)
    settled_list   = await emby.get_managed_channels()
    settled_by_num = {c["ChannelNumber"]: c for c in settled_list if c.get("ChannelNumber")}

    async def _correct_one(chno: str, sid: str, pid: str | None):
        ech = settled_by_num.get(chno)
        if not ech:
            return None
        current = ech.get("ListingsChannelId")
        if pid and current != sid:
            try:
                await emby.push_channel_mapping(pid, ech["ManagementId"], sid)
                await emby.clear_channel_images(ech["Id"])
                return {"name": chno, "station_id": sid, "status": "corrected"}
            except Exception as exc:
                return {"name": chno, "station_id": sid, "status": "failed", "error": str(exc)}
        if not pid and current:
            try:
                await emby.clear_channel_mapping(any_provider_id, ech["ManagementId"])
                await emby.clear_channel_images(ech["Id"])
                return {"name": chno, "station_id": sid, "status": "corrected"}
            except Exception as exc:
                return {"name": chno, "station_id": sid, "status": "failed", "error": str(exc)}
        return None

    correction_results = await asyncio.gather(
        *(_correct_one(chno, info["station_id"], lineup_for_station.get(info["station_id"]))
          for chno, info in station_map.items() if chno not in preserved_chnos)
    )

    # station_map only covers channels EPGmatcharr has a GN id for, keyed by the
    # channel number unique to each physical channel. Emby's own auto-match runs
    # on EVERY unmapped channel once a provider is active, regardless of whether
    # this code has an opinion about it -- so a channel with no GN id at all can
    # still end up with guessed guide data, and because we now join by channel
    # number rather than name, this correctly reaches a channel even when its
    # display name collides with a different, GN-matched channel (e.g. a SiriusXM
    # audio channel literally named "CNN" no longer hides behind the real "CNN").
    # Channels in an Emby-Sync-excluded group are skipped here too -- "never push
    # to Emby" means never clearing them either, not just never mapping them.
    # Scoped to active_tuner_ids -- a channel on a tuner this run isn't managing
    # (e.g. a dummy/Teamarr tuner with no GN ids of its own) is left completely
    # alone regardless of what mapping it currently has (epgmatcharr-nh7: this is
    # exactly the bug where a Gracenote-tuner push cleared custom XMLTV mappings
    # on an unrelated tuner's channels).
    async def _clear_unknown(ech: dict):
        chno = ech.get("ChannelNumber")
        if not chno or chno in station_map or chno in excluded_chnos or not ech.get("ListingsChannelId"):
            return None
        if _tuner_id_for(ech, known_tuner_ids) not in active_tuner_ids:
            return None
        try:
            await emby.clear_channel_mapping(any_provider_id, ech["ManagementId"])
            await emby.clear_channel_images(ech["Id"])
            return {"name": ech["Name"], "station_id": None, "status": "corrected"}
        except Exception as exc:
            return {"name": ech["Name"], "station_id": None, "status": "failed", "error": str(exc)}

    unknown_results = await asyncio.gather(*(_clear_unknown(ech) for ech in settled_list)) if any_provider_id else []

    corrections = [r for r in correction_results if r] + [r for r in unknown_results if r]

    mapped  = [r for r in results if r["status"] == "mapped"]
    failed  = [r for r in results if r["status"] == "failed"] + [r for r in corrections if r["status"] == "failed"]
    cleared = [r for r in results if r["status"] == "cleared"]
    skipped = [r for r in results if r["status"] == "skipped"]
    left_unchanged = [r for r in results if r["status"] == "left_unchanged"]
    corrected_count = len([r for r in corrections if r["status"] == "corrected"])

    # clear_channel_images only drops stale artwork -- it doesn't fetch a
    # replacement. Emby's own "Refresh Guide" task is what re-fetches images
    # matching each channel's now-current mapping, so anything this run touched
    # actually shows the right logo instead of a blank one.
    guide_refreshed = await emby.refresh_guide()

    return {
        "mapped_count":     len(mapped),
        "cleared_count":    len(cleared),
        "corrected_count":  corrected_count,
        "failed":           failed,
        "skipped_count":    len(skipped),
        "left_unchanged_count": len(left_unchanged),
        "excluded_count":   len(excluded_chnos),
        "tuners_fixed":     tuners_fixed,
        "guide_refreshed":  guide_refreshed,
        "zip_codes_used":   zip_codes,
        "selected_lineups": [
            {"listings_id": lid, "name": candidates[lid]["name"], "zip_code": candidates[lid]["zip_code"]}
            for lid in selected
        ],
    }


# ── Manual single-channel overrides ─────────────────────────────────────────
# For channels the automatic coverage flow can't resolve (or gets wrong) --
# search across Emby's currently-configured (real, non-trial) listing providers
# and map or clear one channel directly, bypassing discovery/coverage entirely.

async def search_stations(query: str) -> list[dict]:
    """Station candidates matching query by name, across every listing provider
    Emby currently has configured. Only searches what Emby can actually accept a
    mapping against right now -- not the full GN Station DB -- since a manual
    override still has to be a real station on a real, active Emby lineup."""
    q = query.strip().lower()
    if len(q) < 2:
        return []
    emby = EmbyClient()
    providers = await emby.list_providers()

    async def _search_one(provider: dict) -> list[dict]:
        pid = provider["Id"]
        options = await emby.get_channel_mapping_options(pid)
        return [
            {"provider_id": pid, "provider_name": provider.get("Name", ""), "station_id": opt["Id"], "name": opt.get("Name", "")}
            for opt in options if q in opt.get("Name", "").lower()
        ]

    results_per_provider = await asyncio.gather(*(_search_one(p) for p in providers))
    seen: set[tuple[str, str]] = set()
    results: list[dict] = []
    for provider_results in results_per_provider:
        for r in provider_results:
            key = (r["provider_id"], r["station_id"])
            if key in seen:
                continue
            seen.add(key)
            results.append(r)
    return results[:50]


async def map_channel(channel_number: str, provider_id: str, station_id: str) -> dict:
    """Manually maps a single Emby channel (by channel number) to a station id on
    the given provider. Bypasses the normal discovery/coverage flow entirely --
    for the channels that flow can't resolve on its own."""
    emby = EmbyClient()
    channels = await emby.get_managed_channels()
    ech = next((c for c in channels if c.get("ChannelNumber") == channel_number), None)
    if not ech:
        raise ValueError(f"Channel {channel_number} not found in Emby's channel scan")
    await emby.push_channel_mapping(provider_id, ech["ManagementId"], station_id)
    await emby.clear_channel_images(ech["Id"])
    return {"ok": True}


async def clear_channel(channel_number: str) -> dict:
    """Clears whatever mapping a single Emby channel currently has, explicit or
    Emby's own auto-match -- for a channel the user wants EPGmatcharr to leave
    alone, or a bad match they want removed without waiting for the next push."""
    emby = EmbyClient()
    channels = await emby.get_managed_channels()
    ech = next((c for c in channels if c.get("ChannelNumber") == channel_number), None)
    if not ech:
        raise ValueError(f"Channel {channel_number} not found in Emby's channel scan")
    providers = await emby.list_providers()
    if not providers:
        raise ValueError("No listing providers configured in Emby")
    await emby.clear_channel_mapping(providers[0]["Id"], ech["ManagementId"])
    await emby.clear_channel_images(ech["Id"])
    return {"ok": True}
