# EPGmatcharr — User Guide

This guide covers the full EPGmatcharr workflow from initial setup through committing EPG assignments back to Dispatcharr.

---

## Table of Contents

1. [Initial Setup](#1-initial-setup)
2. [EPG Sources and Filters](#2-epg-sources-and-filters)
3. [Workflow A — Replace Existing EPG](#3-workflow-a--replace-existing-epg)
4. [Workflow B — Match Unassigned Channels](#4-workflow-b--match-unassigned-channels)
5. [Reading Match Results](#5-reading-match-results)
6. [Now Playing and Stream Preview](#6-now-playing-and-stream-preview)
7. [EPG Guide](#7-epg-guide)
8. [GN Station Matcher](#8-gn-station-matcher)
9. [Inline Channel Renaming](#9-inline-channel-renaming)
10. [Committing Assignments](#10-committing-assignments)
11. [EPG Cache Warming](#11-epg-cache-warming)
12. [Emby Guide Sync](#12-emby-guide-sync)

---

## 1. Initial Setup

### Connection

On first launch, EPGmatcharr opens directly to the setup screen.

![Blank setup screen](screenshots/ug-01-setup-blank.png)

Enter your Dispatcharr base URL and API token, then click **Test Connection**.

![Connection setup filled in](screenshots/ug-02-connection-setup.png)

A green confirmation appears when the connection succeeds.

![Successful connection test](screenshots/ug-03-test-connection.png)

Click **Connect** to save and open the main app.

![Main app after connecting](screenshots/ug-04-main-app.png)

### EPG Cache Settings

Open **Settings** (gear icon, top right) to configure how EPGmatcharr downloads EPG data.

![Settings panel](screenshots/ug-05-settings.png)

- **EPG Cache TTL** — how often the cache refreshes (default: 1 hour)
- **EPG Window** — how many days of data to download per source (default: 7 days)
- **Backfill GN IDs on commit** — writes the matched EPG entry's Gracenote station ID back to any channel that has no `tvc_guide_stationid` set in Dispatcharr
- **Backfill tvg-id on commit** — writes the matched EPG entry's tvg-id back to any channel that has no `tvg_id` set; use this to convert call-sign channels to Gracenote station ID format
- **Enable EPG Guide** — show or hide the EPG Guide tab; disable for a lighter experience if you only need channel matching

Click **Save EPG Settings** to apply.

![EPG settings saved](screenshots/ug-06-epg-saved.png)

### GN Station DB

The GN Station DB is a locally-cached SQLite database of Gracenote station IDs and call signs, built weekly from Jesmann's EPG sources. It enables **GN DB bridge matching** — resolving a channel's call-sign tvg-id (e.g. `KVUEDT`) to a numeric Gracenote station ID (e.g. `33585`) so it can match against EPG sources that use station IDs as their tvg-id.

In Settings, the **GN Station DB** card shows the current database version and station count. Click **Update GN Station DB** to download the latest build.

Bridge matches appear in the candidate dropdown with the tier label **GN bridge**.

### Login Credentials (Optional)

If you want to password-protect EPGmatcharr, set credentials in Settings. Once saved, all pages require login. Leave blank to skip authentication entirely.

![Credentials form](screenshots/ug-07-credentials-filled.png)

After credentials are saved, the app redirects to the login screen.

![Login screen](screenshots/ug-08-login-screen.png)

![Login form filled](screenshots/ug-09-login-filled.png)

### EPG Ready

After startup (or after the cache warms), the **EPG ready** pill appears in the header. The app is now ready to run matches.

![EPG ready pill](screenshots/ug-10b-epg-ready.png)

Hovering the pill shows the warming status of each configured EPG source.

![EPG ready hover](screenshots/ug-10c-epg-ready-hover.png)

---

## 2. EPG Sources and Filters

### EPG Source Buttons

At the top of the page, click one or more EPG source buttons to select which sources EPGmatcharr will match against. Selected sources are highlighted.

![EPG source selected](screenshots/ug-11-source-selected.png)

You can select multiple sources simultaneously — the matcher searches all of them.

The buttons are listed in the same priority order you've set for each source in Dispatcharr (highest priority first), so your preferred sources are always easiest to find.

### TVG-ID Filter

The **TVG-ID filter** restricts matches to EPG entries whose `tvg_id` contains the entered string. Use this to limit matching to a specific country or region.

Examples:
- `.us` — US stations only
- `.ca` — Canadian stations only
- `.uk` — UK stations only

Leave blank to match against the full EPG source without restriction.

### Prefer CALLSIGN-DT

The **Prefer -DT** checkbox appears below the TVG-ID filter. When checked, EPGmatcharr breaks score ties in favor of `CALLSIGN-DT` variants (e.g. `KVUE-DT`) over bare callsigns (e.g. `KVUE`). This is recommended when matching against Gracenote EPG sources (such as Jesmann 7day GN), where the `-DT` designation appears in the EPG entry's name field rather than its tvg-id.

---

## 3. Workflow A — Replace Existing EPG

Use this workflow when you want to re-match channels that already have an EPG source assigned — for example, migrating channels from one EPG source to another.

### Step 1 — Set Filters

Leave **Unassigned only** unchecked. In the **Filter by EPG source** dropdown, select the EPG source whose channels you want to replace.

![Filter: Replace EPG](screenshots/ug-14-filter-replace-epg.png)

### Step 2 — Load Channels

Click **Load Channels** (or **Reload**). The table populates with all channels currently assigned to that source.

![553 channels loaded, all assigned to IPTVBoss](screenshots/ug-15-channels-loaded-replace.png)

In this example, 553 channels are assigned to "IPTVBoss - EPG Only" — these are the channels we want to re-match.

### Step 3 — Select Channels

Click **Select all** to check all channels, then click **Run Match on N selected**.

![All 553 channels selected](screenshots/ug-16-channels-selected.png)

### Step 4 — Run Match

The matcher runs against all selected EPG sources. A spinner shows progress.

![Matching in progress](screenshots/ug-17-match-running.png)

### Step 5 — Review Results

When matching completes, each channel shows a confidence badge and the top match candidate.

![Match results with High confidence badges](screenshots/ug-18-match-results.png)

See [Reading Match Results](#5-reading-match-results) for details on badges and dropdowns.

### Step 6 — Commit

Click **Commit N assignments** to send all selected matches to Dispatcharr.

A confirmation message appears when complete.

![398 channels assigned successfully](screenshots/ug-25-commit-confirm.png)

---

## 4. Workflow B — Match Unassigned Channels

Use this workflow for a fresh setup — channels with no EPG source at all.

### Step 1 — Set Filters

Check **Unassigned only**. Leave the EPG source filter set to "Filter by EPG source" (no selection).

![Filter: Unassigned only](screenshots/ug-12-filter-unassigned.png)

### Step 2 — Load Channels

Click **Load Channels**. Only channels with no EPG assignment appear. The count next to the channel total shows how many have no EPG.

![564 channels loaded, all No EPG](screenshots/ug-13-channels-loaded.png)

### Step 3 — Select and Match

Click **Select all** (or **Select unassigned**), then **Run Match on N selected**.

![Matching unassigned channels](screenshots/ug-28-unassigned-matching.png)

### Step 4 — Review and Commit

Review the results, make any manual adjustments, and click **Commit N assignments**.

![Unassigned match results — 42 matched](screenshots/ug-29-unassigned-results.png)

![42 channels assigned successfully](screenshots/ug-30-unassigned-commit.png)

After commit, matched channels show **Assigned** in the EPG Status column and disappear from the unassigned filter on next reload.

---

## 5. Reading Match Results

### Confidence Badges

Each matched channel receives one of two confidence badges:

| Badge | Meaning |
|---|---|
| **High** | Strong match — channel is auto-selected for commit |
| **Review** | Lower confidence — review before committing |

![High and Review badges side by side](screenshots/ug-19-match-confidence-mix.png)

**High** channels are checked by default. **Review** channels are unchecked — you decide whether to include them.

Use **Select all high** (top right) to select only the High-confidence matches in bulk.

### Manual Override Dropdown

Every matched channel has a dropdown showing the top candidate and its match score. Click the dropdown to see all ranked alternatives.

![Candidate dropdown open](screenshots/ug-20-match-dropdown.png)

Each candidate shows:
- **EPG entry name** — the matched program guide entry
- **TVG-ID** — the identifier used in the EPG source
- **Score** — match confidence percentage
- **Match tier** — how the match was found:

| Tier | Label | Description |
|---|---|---|
| 1 | `tvg_id` | Exact tvg-id match |
| 2a | `GN exact` | Both channel and EPG have matching `tvc_guide_stationid` |
| 2b | `GN fwd` | Channel's `tvc_guide_stationid` matches EPG's tvg-id |
| 2c | `GN rev` | Channel's tvg-id matches EPG's `tvc_guide_stationid` |
| 2d | `GN bridge` | Call-sign tvg-id resolved to station ID via GN Station DB |
| 3 | `Callsign` | K/W callsign extracted from channel name or tvg-id |
| 4 | `Fuzzy` | Normalized name fuzzy match |

Select a different candidate from the list to override the automatic choice before committing.

### Channel Count

Matched channels that could not be found at all show **No Match** in the EPG Status column and are not selected for commit.

---

## 6. Now Playing and Stream Preview

### Now Playing

After matching, each row shows the current program from the EPG cache under the match candidate name. This requires the EPG cache to be warmed for that source.

![Now Playing links visible in results](screenshots/ug-21-now-playing-top.png)

Click a **Now Playing** link to see the full current program title and time.

![Now Playing popup showing current show](screenshots/ug-22-now-playing-popup.png)

> **Note:** If Now Playing shows "no program data," the EPG cache may still be warming. Wait for the **EPG ready** pill and reload.

### Stream Preview

The play button (▷) on the right side of each row opens a live stream preview directly from Dispatcharr.

![Video player popup with live stream](screenshots/ug-23-video-player.png)

The small number below the ▷ button shows how many stream URLs Dispatcharr has configured for that channel.

![Stream count numbers under play buttons](screenshots/ug-24-play-button-count.png)

EPGmatcharr supports both HLS and MPEG-TS streams. Click outside the player or press Escape to close it.

---

## 7. EPG Guide

The **EPG Guide** tab shows a live programme grid for all channels that have EPG assignments. Click the **EPG Guide** tab at the top of the page to switch to it.

![EPG Guide tab](screenshots/ug-epg-guide.png)

The guide displays current and upcoming programmes in a scrollable grid. Click any programme block to see full details.

> **Note:** The EPG Guide can be disabled in Settings → **Enable EPG Guide** if you don't need it. Disabling it hides the tab entirely and skips guide data fetches, making the app lighter.

---

## 8. GN Station Matcher

The **GN Matcher** tab assigns Gracenote station IDs (`tvc_guide_stationid`) directly to your Dispatcharr channels. This is separate from EPG source matching — GN station IDs are the numeric identifiers Gracenote uses internally (e.g. `33585` for KVUE-DT). Setting them enables accurate matching against Gracenote-based EPG sources.

### Setup Card

Before running a match, configure the options in the setup card at the top of the GN Matcher tab:

- **Channel group** — restrict matching to channels in a specific group (or leave as "All groups")
- **Country filter** — limit GN station candidates to a specific country (US, GB, DE, NL, etc.). Leave as "All countries" to see candidates from every country in the GN Station DB

Click **Run Match** to score all channels.

### Match Results

Each channel row shows:

- **Score bar** — color-coded confidence indicator (green = high, yellow = medium, orange = low)
- **Confidence badge** — High, Medium, Low, None, or Has GN (already assigned)
- **Top GN candidate** — the station EPGmatcharr recommends, with its call sign and name
- **Country badge** — small flag code (US, GB, DE, etc.) on each candidate showing which country the station is from

High-confidence matches are auto-checked. Medium and Low matches are unchecked — review them before committing.

### Overriding a Match

Click the **search icon** (🔍) on any row to open the candidate picker. It shows all scored candidates for that channel plus a search box for manual lookup. Selecting a candidate from the list overrides the automatic choice.

The picker automatically flips upward when the row is near the bottom of the screen.

### Clearing a Bad Mapping

If a channel already has a GN station ID assigned (**Has GN** badge) but the assignment is wrong, click the **trash icon** (🗑) on that row to clear it. The channel returns to the unassigned pool so it can be re-matched correctly. An incomplete guide is better than a guide with wrong station data.

### Committing

Click **Commit N assignments** to write all checked GN station IDs to Dispatcharr in one batch. The count reflects the number of channels currently checked.

After committing, matched channels show the **Has GN** badge on the next match run.

### Recheck Existing Matches

The GN Station DB is updated weekly, and station entries occasionally change — for example, a channel matched to a bare call sign (`KVUE`) before a `-DT`/`-CD`/`-LD`-suffixed entry (`KVUE-DT`) existed for that station. Click **Recheck Existing Matches** to re-score every channel that already has a GN station ID and surface only the ones where a clearly better candidate now exists.

Unlike a normal run, Recheck only returns a channel if the new top candidate is both high-confidence and different from what's currently assigned — so the result list is just the stale entries worth fixing, not your whole channel list. Review and commit the corrected suggestions the same way as any other match run.

---

## 9. Inline Channel Renaming

When a channel name in Dispatcharr doesn't match the actual channel, you can rename it directly in the match table before committing — no need to go back into Dispatcharr manually.

### Identifying a mismatch

If the EPG match returns a different name than what Dispatcharr has, the stream preview can confirm which is correct. In this example, Dispatcharr has the channel named "El Rey Network" but the matcher returned "El Rey Rebel."

![El Rey Network matched to El Rey Rebel with Review badge](screenshots/ug-27-rename-match.png)

Opening the stream preview confirms the channel is broadcasting with an El Rey Rebel watermark — the Dispatcharr name is wrong.

![Stream preview showing El Rey Rebel watermark](screenshots/ug-33-rename-video.png)

### Renaming the channel

Hover over any channel name in the table to reveal the pencil (✏️) icon. A **Rename channel** tooltip appears.

![Pencil icon with Rename channel tooltip](screenshots/ug-34-rename-hover.png)

Click the pencil to open the inline edit field. The current Dispatcharr name is pre-filled.

![Inline edit field open with current channel name](screenshots/ug-35-rename-epg.png)

Click **Use EPG** to fill the field with the matched EPG name automatically, or type a name manually. Click **Revert** to cancel and restore the original name.

![Edit field updated with EPG name El Rey Rebel](screenshots/ug-36-rename-applied.png)

The rename commits to Dispatcharr alongside the EPG assignment when you click **Commit**. No separate save step is needed.

---

## 10. Committing Assignments

The **Commit** button sends all checked channel-to-EPG assignments to Dispatcharr in one batch. Dispatcharr updates immediately — no restart required.

Channels where you edited the name inline also have their names updated at commit time.

After a successful commit, the status column updates from **No EPG** / **Assigned (old source)** to **Assigned** with a green indicator.

To verify in Dispatcharr, switch to the Dispatcharr tab and hover the EPG icon on any recently matched channel.

![Dispatcharr showing updated EPG tooltip](screenshots/ug-26-dispatcharr-epg-updated.png)

---

## 11. EPG Cache Warming

EPGmatcharr downloads EPG data from all configured sources in the background at startup and before each TTL expiry.

While warming is in progress, the header shows a **Warming EPG X/Y** pill.

![Warming EPG 3/5 pill in header](screenshots/ug-31-epg-warming.png)

Click or hover the pill to see the per-source status.

![Warming EPG popover with per-source status](screenshots/ug-32-epg-warming-popover.png)

- **Green circle** — source fully loaded
- **Spinning circle** — source still downloading

You can run a match while warming is in progress, but **Now Playing** data will not appear for sources that haven't finished loading yet. The match algorithm itself uses a separate index and is not affected by warming status.

---

## 12. Emby Guide Sync

If you run **Emby** with its built-in Gracenote (embygn) Live TV guide, EPGmatcharr can configure it for you automatically — discovering the Gracenote lineups your channels need, adding the minimal set to Emby, and mapping each channel to the correct station ID. This is a separate integration from EPG source matching against Dispatcharr's own EPG data; it drives Emby's own guide provider directly.

### Prerequisite: GN Station IDs

Emby Sync maps channels using the GN station IDs already assigned in EPGmatcharr (`tvc_guide_stationid`). Run the **GN Matcher** (§8) first and commit station IDs for the channels you want synced — a channel with no GN station ID is skipped entirely and never touched in Emby.

### Connecting Emby

In **Settings**, scroll to the **Emby Guide (embygn)** card:

- **Emby URL** — e.g. `http://192.168.1.100:8096`
- **API Key** — from Emby's Dashboard → Advanced → API Keys
- **ZIP code(s)** *(optional)* — EPGmatcharr auto-detects the markets it needs from your channels' call signs using public FCC station data, so this can usually be left blank. Add a ZIP here only if a market isn't being picked up automatically (e.g. a channel with an unusual or missing call sign).
- **Country** — US or CA

Click **Test Connection** to verify Emby is reachable, then **Save**.

### Preview Coverage

Open the **Emby Sync** tab and click **Preview Coverage**. This is fully reversible — nothing is changed on your Emby server. EPGmatcharr will:

1. Auto-derive the ZIP codes/markets needed from your channels' call signs (unioned with any ZIP codes you entered manually in Settings).
2. Discover available Gracenote lineups for those markets and pick the minimal set that covers your matched channels (greedy set-cover — as few lineups as possible).
3. Report what would happen if you pushed.

The summary shows:

| Card | Meaning |
|---|---|
| **Would be mapped** | Channels that will get a station mapping pushed to Emby |
| **No GN ID yet** | Channels with no GN station ID — skipped; run GN Matcher first |
| **No lineup covers it** | A GN station ID exists, but none of the discovered lineups carry that station — try adding a ZIP code for that market in Settings |
| **Not found in Emby** | The channel wasn't found in Emby's channel scan — usually a channel-number mismatch between Dispatcharr and Emby |

Below the summary, the **markets detected** line shows how many ZIP codes were used and how many were auto-detected vs. entered manually, followed by the list of Gracenote lineups selected and how many channels each one covers.

### Pushing

Click **Push N Mappings to Emby** to apply. EPGmatcharr will:

- Add the selected Gracenote lineups to Emby as listing providers.
- Disable Emby's built-in "match channels by number" auto-matching, which otherwise silently overwrites unmapped channels with whatever the new provider happens to call that same channel number.
- Map each channel to its known GN station ID.
- Wait briefly, then re-check and correct anything Emby's own background call-sign auto-matching changed in the meantime.
- Clear the mapping on any Emby channel that isn't in EPGmatcharr's channel list at all (channels with no GN ID are never touched, and channels not seen at all are actively cleared) — so channels never end up with stale or coincidentally-wrong artwork/guide data.
- Clear cached channel artwork for anything that changed, and trigger Emby's **Refresh Guide** task so the new mappings and images take effect.

Push is safe to re-run — it's idempotent and will only change what's actually wrong or out of date.

### Troubleshooting

- **"No ZIP codes could be determined"** — none of your channels' call signs matched the bundled FCC market database. Add a ZIP code manually in Settings for the market(s) you need.
- **Channels stuck in "No lineup covers it"** — the discovered lineups for the auto-detected markets don't happen to carry that station. Add the correct market's ZIP code manually in Settings and re-run Preview.
- **Channels in "Not found in Emby"** — confirm the channel exists in Emby's Live TV setup with the same channel number as in Dispatcharr; EPGmatcharr matches by channel number, not name, since duplicate names (e.g. a TV channel and a radio channel both called "CNN") aren't reliable.

---

## Tips

- **TVG-ID filter** is the most effective way to improve match accuracy. Setting `.us` when matching US locals dramatically reduces false positives from international entries with the same channel name.
- **Select all high → Commit** is the fastest path for a clean match run. Review channels can be addressed in a second pass.
- **Re-running a match** after a manual override resets all overrides. Lock in your overrides and commit before re-running.
- **EPG cache clears on container restart** only if `epg_cache.json` is deleted from the data volume. Normal restarts restore from the saved cache file within seconds.
- **GN Matcher country filter** is especially useful for international users — set it to your country before running to avoid seeing US or UK candidates mixed in with local stations.
- **Prefer -DT** in EPG Matcher is recommended any time you are matching against a Gracenote EPG source (Jesmann 7day GN). Without it, `KVUE` and `KVUE-DT` score identically and the wrong one may be picked.
- **Clear before re-matching** — if a channel has a bad GN ID (Has GN badge), use the trash icon to clear it first, then re-run the GN Matcher so it gets properly scored.
- **Recheck Existing Matches** periodically (e.g. after each weekly GN Station DB update) to catch channels whose station ID has gone stale.
- **Emby Sync needs GN station IDs first** — run and commit GN Matcher before your first Emby Sync push, or every channel will show up as "No GN ID yet."
- **Emby ZIP codes are usually automatic** — only add one manually in Settings if Preview Coverage shows channels stuck in "No lineup covers it" for a specific market.
