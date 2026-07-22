"""Shared epg.guru directory-index scraping for tools/build_gn_db.py and
tools/build_epg_cache.py -- both need the current list of per-country
7daygracenote/7dayiptv files, and both need to exclude the same set of
non-country regional/combined bundle files that live in the same directory
listing (FullGuide is the best known example -- all countries combined into
one file -- but the index also carries a handful of test fixtures and
multi-country regional bundles). One discovery function, one exclude list,
used by both build scripts so a new country epg.guru adds shows up in the
GN Station DB and the epg.guru programme cache on their next scheduled run
with zero code changes either place.
"""

import re

import httpx

# Non-country entries observed in the epg.guru/cdn.epg.guru directory
# listings alongside real per-country files. FullGuide is tracked
# separately as its own market (see build_epg_cache.py); the rest are
# multi-country regional bundles or test fixtures, never a single country.
EXCLUDED_BUNDLES = {
    "FullGuide", "Sports", "Test", "USTest",
    "Caribbean", "Europe", "NorthAmerica", "LatinSouthAmerica",
}

_HREF_RE = re.compile(r'href="([A-Za-z0-9\-]+)\.xml\.gz"')


def discover_countries(client: httpx.Client, tier: str) -> list[tuple[str, str]]:
    """Returns [(name, url), ...] for every real per-country/market file
    currently listed under https://epg.guru/{tier}/ -- excludes
    EXCLUDED_BUNDLES. Raises on index-page fetch failure; callers should
    let one bad tier fail loudly rather than silently building from an
    empty list.
    """
    resp = client.get(f"https://epg.guru/{tier}/", timeout=30.0)
    resp.raise_for_status()
    names = sorted(set(_HREF_RE.findall(resp.text)))
    return [
        (name, f"https://epg.guru/{tier}/{name}.xml.gz")
        for name in names
        if name not in EXCLUDED_BUNDLES
    ]
