"""
EPG Matcher Service

Tiered matching logic:
  Tier 1  — exact tvg_id match                             → score 1.0
  Tier 2a — GN exact (ch.tvc_stationid == epg.tvc)  → score 0.98
  Tier 2b — GN fwd   (ch.tvc_stationid == epg.tvg)  → score 0.95
  Tier 2c — GN rev    (ch.tvg_id == epg.tvc)              → score 0.93
  Tier 2d — GN bridge (GN DB: ch.tvg_id → station_id)     → score 0.91
  Tier 3  — callsign match (K/W callsigns)                  → score 0.92
  Tier 2e — epg.guru embedded code (ch.tvg_id == code inside epg's "Name(CODE).cc" tvg_id) → score 0.90
  Tier 4  — fuzzy normalized name match                     → score 0.0–0.89

When multiple candidates tie on score, prefer (in order): the entry already
assigned to this channel in Dispatcharr, then a non-placeholder-coded entry
(epg.guru's own "GxxxxNWD"-style synthetic codes for entries it couldn't
confidently tag), then the existing -DT tiebreak.

Confidence thresholds:
  high   ≥ 0.90
  medium ≥ 0.65
  low    ≥ 0.30
  none   < 0.30
"""

import asyncio
import difflib
import logging
import re
from typing import Optional

from gn_station_db import get_all_callsigns, lookup_gn_id

logger = logging.getLogger(__name__)

_NOISE_TOKENS = re.compile(
    r"\b(hd|fhd|uhd|4k|sd|east|west|channel|tv|network|plus|us|usa)\b",
    re.IGNORECASE,
)
# '+' and '&' are kept (not treated as noise) -- they distinguish real,
# differently-tiered channels that would otherwise collide after stripping,
# e.g. "AMC" vs "AMC+", or "Crime + Investigation" vs a differently-coded
# "Crime & Investigation" duplicate entry. See epgmatcharr-sxi.
_NON_ALPHA  = re.compile(r"[^a-z0-9+&]")
_WHITESPACE = re.compile(r"\s+")

# epg.guru tvg_ids embed a short, stable station code just before the country
# suffix, e.g. "MTV-MusicTelevision(MTV).us" or "Tr3s:MTV,MusicayMas(TR3S).us".
# Display names vary a lot more than tvg_ids across renames/rewording, so this
# code is often the only reliable link left once the full tvg_id string and
# display name have both drifted apart. Only trusted when unique within the
# filtered EPG set (built in _compute_match) so an ambiguous/repeated code
# never causes a wrong-channel collision.
_EMBEDDED_CODE_RE   = re.compile(r'\(([A-Za-z0-9]{2,10})\)\.[a-z]{2,3}$', re.IGNORECASE)
_COUNTRY_SUFFIX_RE  = re.compile(r'\.[a-z]{2,3}$', re.IGNORECASE)

# epg.guru's own placeholder code for entries it couldn't confidently tag with
# a real station code -- observed as "G<abbrev><1|2>WD", e.g. MTV's real US
# feed sits at "MTV-MusicTelevision(MTV).us" while a same-named but wrong/
# lower-quality duplicate sits at "MTV(GMTV2WD).us"; same pattern recurs for
# Cartoon Network (GCTN2WD), Cinemax (GMAX1WD), Comedy (GCOM2WD), etc. These
# duplicates tie on display-name score with the real entry, so they need an
# explicit tiebreak demotion rather than silently winning on iteration order.
_PLACEHOLDER_CODE_RE = re.compile(r'\(G[A-Z0-9]*[12]WD\)\.[a-z]{2,3}$', re.IGNORECASE)


def _is_placeholder_code(tvg_id: Optional[str]) -> bool:
    return bool(tvg_id and _PLACEHOLDER_CODE_RE.search(tvg_id))

_CALLSIGN_RE    = re.compile(r'^[KWkw][A-Za-z]{2,3}$')  # real US callsigns are 3-4 chars total
_CALLSIGN_SPLIT = re.compile(r'[\s\-_./|]+')

CONF_HIGH   = 0.90
CONF_MEDIUM = 0.65
CONF_LOW    = 0.30

MAX_CANDIDATES = 8
FUZZY_CUTOFF   = 0.50

# difflib.SequenceMatcher.ratio() (2*M/(len(a)+len(b))) overweights short
# normalized names sharing just one common/generic word -- e.g. normalized
# "world" (5 chars) vs "zee zee world" (13 chars) scores 0.556 purely off
# the shared suffix "world", clearing FUZZY_CUTOFF with zero real signal
# about which channel it actually is. Verified this isn't fixable by noise-
# token stripping alone: "Newsworld" (9 chars, a single compound token) has
# the same problem via its own "world" suffix. Require the shorter of the
# two normalized names to have real length before trusting the ratio at
# all, unless the match is close to exact (typo-level, not "shares one
# short word"). See epgmatcharr-sxi.
_FUZZY_MIN_SHORT_LEN = 10
_FUZZY_NEAR_EXACT    = 0.95


def _fuzzy_trusted(norm_a: str, norm_b: str, ratio: float) -> bool:
    if ratio >= _FUZZY_NEAR_EXACT:
        return True
    return min(len(norm_a), len(norm_b)) >= _FUZZY_MIN_SHORT_LEN


def _extract_callsign(text: str) -> Optional[str]:
    for token in _CALLSIGN_SPLIT.split(text):
        if _CALLSIGN_RE.match(token):
            return token.upper()
    return None


def _tvg_callsign(tvg_id: str) -> Optional[str]:
    if not tvg_id:
        return None
    base = _CALLSIGN_SPLIT.split(tvg_id)[0]
    return base.upper() if _CALLSIGN_RE.match(base) else None


# Matches -DT at the end of a tvg_id (before any .xx extension), with no digit after DT.
# Used to prefer CALLSIGN-DT over CALLSIGN-DT2 / CALLSIGN-DT3 when scores tie.
_MAIN_DT_RE = re.compile(r'-DT(\.[a-z]{2,3})?$', re.IGNORECASE)
_SUB_DT_RE  = re.compile(r'-DT\d', re.IGNORECASE)


def _dt_rank(tvg_id: str, name: str = "", prefer_dt: bool = False) -> int:
    """Rank candidates by DT status for tiebreaking.

    prefer_dt=False (default): 0 = CALLSIGN-DT, 1 = everything else.
    prefer_dt=True:            0 = CALLSIGN-DT, 1 = bare callsign, 2 = CALLSIGN-DT2/DT3.
    Checks both tvg_id and EPG entry name. Also checks the first whitespace-token of the
    name separately, so "KVUE-DT Austin ABC" (Gracenote full display-name format) correctly
    gets rank 0 even though '-DT' is not at the end of the full string.
    """
    def _rank_str(s: str) -> int:
        if not s:
            return 1
        if _MAIN_DT_RE.search(s):
            return 0
        # First word handles "KVUE-DT Full Station Name" — $-anchored RE won't match the full string
        first = s.split()[0] if ' ' in s else ''
        if first and _MAIN_DT_RE.search(first):
            return 0
        if prefer_dt and (_SUB_DT_RE.search(s) or (first and _SUB_DT_RE.search(first))):
            return 2
        return 1

    rank = _rank_str(tvg_id or "")
    if rank > 0 and name:
        rank = min(rank, _rank_str(name))
    return rank


def normalize_name(name: str) -> str:
    n = name.lower()
    n = _NOISE_TOKENS.sub(" ", n)
    n = _NON_ALPHA.sub(" ", n)
    return _WHITESPACE.sub(" ", n).strip()


def _confidence(score: float) -> str:
    if score >= CONF_HIGH:   return "high"
    if score >= CONF_MEDIUM: return "medium"
    if score >= CONF_LOW:    return "low"
    return "none"


async def fetch_epg_data(client) -> list[dict]:
    raw = await client.get("/api/epg/epgdata/")
    return raw if isinstance(raw, list) else raw.get("results", [])


async def fetch_channels(client) -> list[dict]:
    channels = []
    page = 1
    while True:
        resp = await client.get(
            "/api/channels/channels/",
            params={"page": page, "page_size": 500},
        )
        if isinstance(resp, list):
            channels.extend(resp)
            break
        results = resp.get("results", [])
        channels.extend(results)
        if not resp.get("next"):
            break
        page += 1
    return channels


def _compute_match(
    filtered_epg: list[dict],
    all_channels: list[dict],
    channel_ids: Optional[list[int]],
    unassigned_only: bool,
    group_id: Optional[int],
    prefer_dt: bool = False,
) -> list[dict]:
    epg_by_tvg_id:    dict[str, dict]         = {}
    epg_by_tvc_id:    dict[str, dict]         = {}  # keyed by tvc_guide_stationid
    epg_by_norm_name: dict[str, list[dict]]   = {}
    epg_by_callsign:  dict[str, list[dict]]   = {}
    _cs_seen:         dict[str, set]           = {}
    _code_seen:       dict[str, set]           = {}  # embedded code -> {epg ids}, to drop ambiguous codes

    # [KWkw][A-Za-z]{2,3} matches any 3-4 letter word starting with K/W, real
    # callsign or not -- e.g. "World", "Was", "Wild", "Kind" are structurally
    # indistinguishable from a real one by regex alone. Cross-check against
    # the GN Station DB's real call signs when it's available (loaded once
    # here, not per-candidate) to filter these out; None means the DB isn't
    # downloaded yet, in which case every candidate is trusted as before
    # rather than rejecting all of them.
    _known_callsigns = get_all_callsigns()

    def _valid_cs(cs: Optional[str]) -> Optional[str]:
        if not cs:
            return None
        if _known_callsigns is not None and cs not in _known_callsigns:
            return None
        return cs

    epg_by_code: dict[str, dict] = {}

    for e in filtered_epg:
        tvg = (e.get("tvg_id") or "").strip()
        if tvg and tvg not in epg_by_tvg_id:
            epg_by_tvg_id[tvg] = e
        tvc = (e.get("tvc_guide_stationid") or "").strip()
        if tvc and tvc not in epg_by_tvc_id:
            epg_by_tvc_id[tvc] = e
        norm = normalize_name(e.get("name", ""))
        if norm:
            epg_by_norm_name.setdefault(norm, []).append(e)
        eid = e.get("id")
        for cs in filter(None, {_valid_cs(_tvg_callsign(tvg)), _valid_cs(_extract_callsign(e.get("name") or ""))}):
            if eid not in _cs_seen.get(cs, set()):
                epg_by_callsign.setdefault(cs, []).append(e)
                _cs_seen.setdefault(cs, set()).add(eid)
        m = _EMBEDDED_CODE_RE.search(tvg)
        if m:
            code = m.group(1).upper()
            seen = _code_seen.setdefault(code, set())
            if eid not in seen:
                seen.add(eid)
                if len(seen) == 1:
                    epg_by_code[code] = e
                else:
                    epg_by_code.pop(code, None)  # ambiguous once a 2nd distinct entry shares this code

    norm_epg_names = list(epg_by_norm_name.keys())

    channels = all_channels
    if channel_ids is not None:
        ch_id_set = set(channel_ids)
        channels = [c for c in channels if c.get("id") in ch_id_set]
    if group_id is not None:
        channels = [c for c in channels if c.get("channel_group_id") == group_id]
    if unassigned_only:
        channels = [c for c in channels if not c.get("epg_data_id")]

    logger.info("[epg_matcher] matching %d channels against %d EPG entries", len(channels), len(filtered_epg))

    results = []
    for ch in channels:
        ch_id   = ch.get("id")
        ch_name = (ch.get("effective_name") or ch.get("name") or "").strip()
        ch_tvg  = (ch.get("effective_tvg_id") or ch.get("tvg_id") or "").strip()
        ch_tvc  = (ch.get("effective_tvc_guide_stationid") or ch.get("tvc_guide_stationid") or "").strip()

        candidates: list[dict] = []
        by_id:      dict[int, dict] = {}  # epg_data_id -> its entry in `candidates`, for the upgrade check below

        def _add(e: dict, score: float, tier: str) -> None:
            eid = e.get("id")
            # A placeholder-coded entry (see _PLACEHOLDER_CODE_RE) ties on an
            # exact display-name match just as easily as the real entry
            # does -- e.g. channel "MTV" vs the wrong "MTV(GMTV2WD).us" both
            # score a full 1.0 on Tier 4's name match, which otherwise beats
            # every other tier's score outright (not just on a tiebreak).
            # Penalize it directly so a same-score placeholder match no
            # longer silently outranks a lower-scored but more specific hit
            # (e.g. Tier 2e correctly finding "MTV - Music Television" via
            # its embedded code at 0.90) -- while still leaving it usable as
            # a fallback if nothing else exists.
            if _is_placeholder_code(e.get("tvg_id")):
                score *= 0.85
            score = round(score, 3)
            existing = by_id.get(eid)
            if existing is not None:
                # A later, independent tier (e.g. Tier 4's fuzzy name match)
                # can legitimately score the same entry higher than an
                # earlier tier did (e.g. Tier 2e's fixed 0.90) -- most often
                # when it's actually an exact name match. Never let an
                # earlier, lower-confidence tier permanently cap a score a
                # later tier would have given it fairly.
                if score > existing["score"]:
                    existing["score"] = score
                    existing["tier"]  = tier
                return
            entry = {
                "epg_data_id": eid,
                "name":        e.get("name", ""),
                "tvg_id":      e.get("tvg_id"),
                "icon_url":    e.get("icon_url"),
                "score":       score,
                "tier":        tier,
                "epg_source_id": e.get("epg_source"),
            }
            by_id[eid] = entry
            candidates.append(entry)

        # Tier 1: exact tvg_id
        if ch_tvg and ch_tvg in epg_by_tvg_id:
            _add(epg_by_tvg_id[ch_tvg], 1.0, "tvg_id_exact")
        # Tier 2a: GN exact — both sides have tvc_guide_stationid
        if ch_tvc and ch_tvc in epg_by_tvc_id:
            _add(epg_by_tvc_id[ch_tvc], 0.98, "gn_exact")
        # Tier 2b: GN fwd — channel tvc matches EPG tvg_id (jesmanns guide format)
        if ch_tvc and ch_tvc in epg_by_tvg_id:
            _add(epg_by_tvg_id[ch_tvc], 0.95, "gn_id")
        # Tier 2c: GN rev — channel tvg_id matches EPG tvc_guide_stationid
        if ch_tvg and ch_tvg in epg_by_tvc_id:
            _add(epg_by_tvc_id[ch_tvg], 0.93, "gn_rev")
        # Tier 2d: GN DB bridge — resolve call sign → station ID via GN DB,
        # then match EPG entries that use the numeric station ID as their tvg_id
        # or tvc_guide_stationid (e.g. Gracenote EPG sources)
        if not candidates or candidates[0]["score"] < CONF_HIGH:
            if ch_tvg and not ch_tvg.isdigit():
                gn_sid = lookup_gn_id(ch_tvg)
                if gn_sid:
                    if gn_sid in epg_by_tvg_id:
                        _add(epg_by_tvg_id[gn_sid], 0.91, "gn_db_bridge")
                    if gn_sid in epg_by_tvc_id:
                        _add(epg_by_tvc_id[gn_sid], 0.91, "gn_db_bridge")
        if not candidates or candidates[0]["score"] < CONF_HIGH:
            ch_cs = _valid_cs(_extract_callsign(ch_name)) or _valid_cs(_tvg_callsign(ch_tvg))
            if ch_cs and ch_cs in epg_by_callsign:
                for e in epg_by_callsign[ch_cs]:
                    _add(e, 0.92, "callsign")
        # Tiers 1-3 above are all cross-checked against a second, independent
        # signal (exact tvg_id/GN station id/a real broadcast callsign), so a
        # >=CONF_HIGH hit here is trustworthy enough to skip the expensive
        # fuzzy pass below. Remember that *before* Tier 2e runs.
        _strong_hit = bool(candidates) and candidates[0]["score"] >= CONF_HIGH

        # Tier 2e: our tvg_id (minus its country suffix) matches the short
        # code epg.guru embeds inside its own tvg_id -- catches renamed/
        # reworded display names (e.g. "MTV 2" vs "MTV2: Music Television")
        # that Tier 4's fuzzy name match can't bridge. Unlike tiers 1-3, this
        # only checks the channel's own tvg_id against one string pulled out
        # of the EPG entry's tvg_id -- if that tvg_id is itself stale/wrong
        # (common on IPTV-provider playlists), this tier has no way to catch
        # that alone. So it must NOT skip Tier 4 below: Tier 4 independently
        # checks the channel's *display name*, and the two get merged and
        # re-scored together, so a bad tvg_id-based guess here still loses to
        # a genuine name match instead of silently winning on its own.
        # NOTE: tried gating this on display-name similarity to catch a
        # stale/mislabeled channel.tvg_id coincidentally matching an
        # unrelated entry's code (e.g. a channel named "NBCSN Northwest"
        # whose tvg_id field was garbage-set to "a3cine.us", landing on
        # Atrescine). Measured it against real data first: the ratio for
        # that bad case (0.25) was *higher* than several genuine matches
        # this tier exists for, e.g. "MTV" vs "MTV - Music Television"
        # (0.26) -- a flat similarity threshold can't separate the two for
        # short names, and would have silently broken the primary case this
        # tier fixes. A bad channel.tvg_id is a pre-existing data-quality
        # risk Tier 1 (exact tvg_id) already carries with no such guard;
        # left equally trusting here rather than adding an unreliable check.
        if not _strong_hit:
            if ch_tvg:
                ch_code = _COUNTRY_SUFFIX_RE.sub("", ch_tvg).upper()
                if len(ch_code) >= 2 and ch_code in epg_by_code:
                    _add(epg_by_code[ch_code], 0.90, "epg_code")
        if not _strong_hit:
            norm_ch = normalize_name(ch_name)
            if norm_ch and norm_epg_names:
                for cn in difflib.get_close_matches(norm_ch, norm_epg_names, n=10, cutoff=FUZZY_CUTOFF):
                    ratio = difflib.SequenceMatcher(None, norm_ch, cn).ratio()
                    if not _fuzzy_trusted(norm_ch, cn, ratio):
                        continue
                    for e in epg_by_norm_name[cn]:
                        _add(e, ratio, "name_fuzzy")

        # Tiebreak order (only among candidates sharing the top score -- this
        # never promotes a lower-scoring candidate over a higher-scoring one):
        # 1) whatever's already assigned to this channel, so a rerun doesn't
        #    churn a correct assignment just because a duplicate EPG row ties;
        # 2) a non-placeholder-coded entry over epg.guru's own synthetic
        #    "GxxxxNWD" duplicates (see _PLACEHOLDER_CODE_RE);
        # 3) the existing -DT preference.
        current_epg_id = ch.get("epg_data_id")
        candidates.sort(key=lambda x: (
            -x["score"],
            0 if x["epg_data_id"] == current_epg_id else 1,
            1 if _is_placeholder_code(x.get("tvg_id")) else 0,
            _dt_rank(x.get("tvg_id") or "", x.get("name") or "", prefer_dt),
        ))
        candidates = candidates[:MAX_CANDIDATES]

        top_score = candidates[0]["score"] if candidates else 0.0
        results.append({
            "channel_id":           ch_id,
            "channel_name":         ch_name,
            "channel_number":       ch.get("channel_number"),
            "channel_uuid":         ch.get("uuid"),
            "channel_tvg_id":       ch_tvg or None,
            "tvc_guide_stationid":  ch_tvc or None,
            "current_epg_data_id":  ch.get("epg_data_id"),
            "channel_group_id":     ch.get("channel_group_id"),
            "confidence":           _confidence(top_score),
            "top_score":            top_score,
            "candidates":           candidates,
        })

    _order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    results.sort(key=lambda x: (_order[x["confidence"]], x["channel_name"].lower()))
    return results


async def run_match(
    source_ids: list[int],
    channel_ids: Optional[list[int]],
    unassigned_only: bool,
    group_id: Optional[int],
    client,
    tvg_id_filter: Optional[str] = None,
    prefer_dt: bool = False,
) -> list[dict]:
    all_epg = await fetch_epg_data(client)
    source_set = set(source_ids) if source_ids else None
    filtered_epg = [
        e for e in all_epg
        if source_set is None or e.get("epg_source") in source_set
    ]
    if tvg_id_filter:
        f = tvg_id_filter.lower()
        filtered_epg = [e for e in filtered_epg if f in (e.get("tvg_id") or "").lower()]

    logger.info("[epg_matcher] %d total EPG entries, %d after filter", len(all_epg), len(filtered_epg))

    all_channels = await fetch_channels(client)
    return await asyncio.to_thread(
        _compute_match,
        filtered_epg, all_channels, channel_ids, unassigned_only, group_id, prefer_dt,
    )


async def search_epg(
    source_ids: list[int],
    query: str,
    limit: int,
    client,
) -> list[dict]:
    all_epg = await fetch_epg_data(client)
    source_set = set(source_ids) if source_ids else None
    filtered = [e for e in all_epg if source_set is None or e.get("epg_source") in source_set]

    q_lower    = query.lower()
    q_norm     = normalize_name(query)
    q_callsign = _extract_callsign(query) or _tvg_callsign(query)

    scored = []
    for e in filtered:
        name = (e.get("name") or "").lower()
        tvg  = (e.get("tvg_id") or "").lower()
        if q_callsign:
            cs = _tvg_callsign(e.get("tvg_id") or "") or _extract_callsign(e.get("name") or "")
            if cs == q_callsign:
                scored.append((0.95, e))
                continue
        if q_lower in name or q_lower in tvg:
            score = 1.0 if name == q_lower or tvg == q_lower else 0.85
        else:
            norm  = normalize_name(e.get("name", ""))
            score = difflib.SequenceMatcher(None, q_norm, norm).ratio()
            if score < 0.45:
                continue
        scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "epg_data_id":   e.get("id"),
            "name":          e.get("name", ""),
            "tvg_id":        e.get("tvg_id"),
            "icon_url":      e.get("icon_url"),
            "score":         round(s, 3),
            "epg_source_id": e.get("epg_source"),
        }
        for s, e in scored[:limit]
    ]
