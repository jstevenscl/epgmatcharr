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
"""

import asyncio
import logging

from dispatcharr_client import DispatcharrClient
from emby_client import EmbyClient
from epg_matcher_service import fetch_channels

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
    """Returns ({channel_number: {name, station_id}} for channels with a GN id
    AND a channel number, total channel count)."""
    client   = DispatcharrClient()
    channels = await fetch_channels(client)
    station_map: dict[str, dict] = {}
    for ch in channels:
        name = (ch.get("effective_name") or ch.get("name") or "").strip()
        sid  = (ch.get("effective_tvc_guide_stationid") or ch.get("tvc_guide_stationid") or "").strip()
        chno = _normalize_channel_number(ch.get("effective_channel_number") or ch.get("channel_number"))
        if chno and sid:
            station_map[chno] = {"name": name, "station_id": sid}
    return station_map, len(channels)


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


async def preview_coverage(zip_codes: list[str], country: str = "US") -> dict:
    """Fully reversible dry run. Categorizes every EPGmatcharr channel with a known
    GN station ID into would_map / no_emby_match / no_lineup_coverage, and reports
    the minimal lineup set that would need to be added to achieve that coverage."""
    if not zip_codes:
        raise ValueError("At least one ZIP code is required")

    emby = EmbyClient()
    tuners_fixed = await emby.disable_auto_match_by_number()

    station_map, total_channels = await _load_dispatcharr_channels()
    needed_ids = {v["station_id"] for v in station_map.values()}

    emby_channels  = await emby.get_managed_channels()
    emby_by_number = {c["ChannelNumber"]: c for c in emby_channels if c.get("ChannelNumber")}

    candidates = await _discover_candidates(emby, zip_codes, country)
    coverage   = await _trial_coverage(emby, candidates, country)
    selected   = _greedy_select(coverage, needed_ids)

    covered_ids: set[str] = set()
    for lid in selected:
        covered_ids |= coverage[lid]["stations"]

    # Preview is fully reversible -- delete every trial provider before returning.
    await _cleanup(emby, [info["provider_id"] for info in coverage.values()])

    would_map: list[dict] = []
    no_emby_match: list[dict] = []
    no_lineup_coverage: list[dict] = []
    for chno, info in station_map.items():
        name, sid = info["name"], info["station_id"]
        if chno not in emby_by_number:
            no_emby_match.append({"name": name, "station_id": sid, "channel_number": chno})
        elif sid not in covered_ids:
            no_lineup_coverage.append({"name": name, "station_id": sid, "channel_number": chno})
        else:
            would_map.append({"name": name, "station_id": sid, "channel_number": chno})

    no_gn_id_count = total_channels - len(station_map)

    return {
        "total_channels":      total_channels,
        "would_map":           would_map,
        "no_gn_id_count":      no_gn_id_count,
        "no_emby_match":       no_emby_match,
        "no_lineup_coverage":  no_lineup_coverage,
        "tuners_fixed":        tuners_fixed,
        "selected_lineups": [
            {"listings_id": lid, "name": candidates[lid]["name"], "zip_code": candidates[lid]["zip_code"],
             "channels_covered": len(coverage[lid]["stations"] & needed_ids)}
            for lid in selected
        ],
        "candidates_tried": len(candidates),
    }


async def push_mappings(zip_codes: list[str], country: str = "US") -> dict:
    """Re-runs discovery, but keeps the winning lineups configured on Emby and
    pushes an explicit ChannelMappings call for every channel that resolves."""
    if not zip_codes:
        raise ValueError("At least one ZIP code is required")

    emby = EmbyClient()

    # Must happen before any provider is added/kept live: with AllowMappingByNumber
    # on, Emby auto-matches any unmapped channel to whatever the active provider
    # calls that same channel NUMBER -- a coincidence, not a station match -- the
    # moment a provider becomes active. That silently corrupts channels this code
    # deliberately chose not to map.
    tuners_fixed = await emby.disable_auto_match_by_number()

    station_map, _ = await _load_dispatcharr_channels()
    needed_ids = {v["station_id"] for v in station_map.values()}

    emby_channels  = await emby.get_managed_channels()
    emby_by_number = {c["ChannelNumber"]: c for c in emby_channels if c.get("ChannelNumber")}

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
          for chno, info in station_map.items())
    )

    # station_map only covers channels EPGmatcharr has a GN id for, keyed by the
    # channel number unique to each physical channel. Emby's own auto-match runs
    # on EVERY unmapped channel once a provider is active, regardless of whether
    # this code has an opinion about it -- so a channel with no GN id at all can
    # still end up with guessed guide data, and because we now join by channel
    # number rather than name, this correctly reaches a channel even when its
    # display name collides with a different, GN-matched channel (e.g. a SiriusXM
    # audio channel literally named "CNN" no longer hides behind the real "CNN").
    async def _clear_unknown(ech: dict):
        chno = ech.get("ChannelNumber")
        if not chno or chno in station_map or not ech.get("ListingsChannelId"):
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
        "tuners_fixed":     tuners_fixed,
        "guide_refreshed":  guide_refreshed,
        "selected_lineups": [
            {"listings_id": lid, "name": candidates[lid]["name"], "zip_code": candidates[lid]["zip_code"]}
            for lid in selected
        ],
    }
