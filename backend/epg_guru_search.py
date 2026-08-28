"""Cross-source epg.guru channel search.

When a channel doesn't match well against a user's configured EPG sources,
this answers the question "is the right tvg_id sitting in a *different*
epg.guru source I haven't added, or is it in one I already have under an
obscure name the matcher missed?" -- independent of what's actually
configured in Dispatcharr, unlike epg_matcher_service.search_epg() which only
searches EPG data Dispatcharr has already ingested from configured sources.

Scoped to just FullGuide + USFast (both tiers) rather than all 10 published
market/tier caches: FullGuide already unions every per-country file epg.guru
publishes (confirmed against real data -- USFast content shows up inside it
too), so together these two markets give full coverage without downloading
markets nobody's asked about. Both tiers are included because they use
different tvg_id conventions (IPTV tier: "Name(CODE).cc"; Gracenote tier:
bare numeric station id) and a channel can exist in one but not the other --
searching only one tier would make a "not found" result unreliable.

Reuses epg_guru_cache's asset download/refresh machinery so this shares the
same on-disk cache and the same "only touch what's actually requested"
behavior -- these 4 files are only fetched the first time a search actually
runs, not eagerly on app startup.
"""

import logging
import re
import sqlite3
from typing import Optional

from epg_guru_cache import _local_path, _maybe_refresh, _URL_TO_MARKET_TIER

# Deliberately a *different* release asset than epg_guru_cache's own
# per-market/tier file: that one is 100s of MB because of its programme
# table; this one (built alongside it -- see tools/build_epg_cache.py's
# _export_channels_only) has just the channel roster, a few MB at most.
def _channels_asset_name(market: str, tier: str) -> str:
    return f"epg_guru_channels_{market}_{tier}.sqlite.gz"

logger = logging.getLogger(__name__)

_MARKETS = ["FullGuide", "USFast"]
_TIERS   = ["7dayiptv", "7daygracenote"]

_COUNTRY_SUFFIX_RE = re.compile(r"\.([a-z]{2,3})$", re.IGNORECASE)

# market/tier -> the "smallest specific" source URL to suggest adding when a
# hit's own market isn't configured. For FullGuide hits we suggest the
# single-country source instead of the ~50-country combined file when we can
# tell which country it's from (via the tvg_id's own country suffix) --
# meaningfully smaller for the user to add. USFast has no per-country split.
_COUNTRY_TO_EPG_GURU_NAME = {
    "us": "UnitedStates",
    "ca": "Canada",
}


def _country_guess(tvg_id: str) -> Optional[str]:
    m = _COUNTRY_SUFFIX_RE.search(tvg_id or "")
    return m.group(1).lower() if m else None


def _suggested_source_url(market: str, tier: str, tvg_id: str) -> str:
    if market == "FullGuide":
        country = _country_guess(tvg_id)
        country_market = _COUNTRY_TO_EPG_GURU_NAME.get(country or "")
        if country_market:
            return f"https://epg.guru/{tier}/{country_market}.xml.gz"
    return f"https://epg.guru/{tier}/{market}.xml.gz"


async def search_epg_guru(
    query: str,
    limit: int,
    configured_source_urls: list[str],
) -> list[dict]:
    """Search FullGuide + USFast (both tiers) for `query` against channel
    name / tvg_id / GN station id. Each result is tagged with whether its
    market/tier is already among `configured_source_urls` (so the caller can
    tell "already in a source you have" from "you'd need to add this").
    """
    configured_market_tiers = {
        _URL_TO_MARKET_TIER[u] for u in configured_source_urls if u in _URL_TO_MARKET_TIER
    }

    q = f"%{query.strip()}%"
    results: list[dict] = []

    for market in _MARKETS:
        for tier in _TIERS:
            asset = _channels_asset_name(market, tier)
            await _maybe_refresh(market, tier, asset=asset)
            local_path = _local_path(asset)
            if not local_path.exists():
                logger.warning("[epg_guru_search] cache unavailable for %s/%s, skipping", market, tier)
                continue

            try:
                conn = sqlite3.connect(str(local_path))
                rows = conn.execute(
                    "SELECT tvg_id, name, tvc_guide_stationid FROM channels "
                    "WHERE name LIKE ? OR tvg_id LIKE ? OR tvc_guide_stationid LIKE ? "
                    "LIMIT ?",
                    (q, q, q, limit),
                ).fetchall()
                conn.close()
            except Exception as exc:
                logger.warning("[epg_guru_search] query failed for %s/%s: %s", market, tier, exc)
                continue

            already_configured = (market, tier) in configured_market_tiers
            for tvg_id, name, gn_id in rows:
                results.append({
                    "tvg_id":               tvg_id,
                    "name":                 name,
                    "gn_id":                gn_id,
                    "market":               market,
                    "tier":                 tier,
                    "already_configured":   already_configured,
                    "suggested_source_url": None if already_configured else _suggested_source_url(market, tier, tvg_id),
                })

    results.sort(key=lambda r: (not r["already_configured"], r["name"] or ""))
    return results[:limit]
