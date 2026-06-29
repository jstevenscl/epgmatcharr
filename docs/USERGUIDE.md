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
7. [Committing Assignments](#7-committing-assignments)
8. [EPG Cache Warming](#8-epg-cache-warming)

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

Click **Save EPG Settings** to apply.

![EPG settings saved](screenshots/ug-06-epg-saved.png)

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

### TVG-ID Filter

The **TVG-ID filter** restricts matches to EPG entries whose `tvg_id` contains the entered string. Use this to limit matching to a specific country or region.

Examples:
- `.us` — US stations only
- `.ca` — Canadian stations only
- `.uk` — UK stations only

Leave blank to match against the full EPG source without restriction.

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

![593 channels loaded, all No EPG](screenshots/ug-13-channels-loaded.png)

### Step 3 — Select and Match

Click **Select all** (or **Select unassigned**), then **Run Match on N selected**.

![Matching unassigned channels](screenshots/ug-28-unassigned-matching.png)

### Step 4 — Review and Commit

Review the results, make any manual adjustments, and click **Commit N assignments**.

![Unassigned match results — 71 matched](screenshots/ug-29-unassigned-results.png)

![49 channels assigned successfully](screenshots/ug-30-unassigned-commit.png)

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
- **Match type** — `Exact`, `Fuzzy`, etc.

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

## 7. Committing Assignments

The **Commit** button sends all checked channel-to-EPG assignments to Dispatcharr in one batch. Dispatcharr updates immediately — no restart required.

Channels where you edited the name inline also have their names updated at commit time.

After a successful commit, the status column updates from **No EPG** / **Assigned (old source)** to **Assigned** with a green indicator.

To verify in Dispatcharr, switch to the Dispatcharr tab and hover the EPG icon on any recently matched channel.

![Dispatcharr showing updated EPG tooltip](screenshots/ug-26-dispatcharr-epg-updated.png)

---

## 8. EPG Cache Warming

EPGmatcharr downloads EPG data from all configured sources in the background at startup and before each TTL expiry.

While warming is in progress, the header shows a **Warming EPG X/Y** pill.

![Warming EPG 3/5 pill in header](screenshots/ug-31-epg-warming.png)

Click or hover the pill to see the per-source status.

![Warming EPG popover with per-source status](screenshots/ug-32-epg-warming-popover.png)

- **Green circle** — source fully loaded
- **Spinning circle** — source still downloading

You can run a match while warming is in progress, but **Now Playing** data will not appear for sources that haven't finished loading yet. The match algorithm itself uses a separate index and is not affected by warming status.

---

## Tips

- **TVG-ID filter** is the most effective way to improve match accuracy. Setting `.us` when matching US locals dramatically reduces false positives from international entries with the same channel name.
- **Select all high → Commit** is the fastest path for a clean match run. Review channels can be addressed in a second pass.
- **Re-running a match** after a manual override resets all overrides. Lock in your overrides and commit before re-running.
- **EPG cache clears on container restart** only if `epg_cache.json` is deleted from the data volume. Normal restarts restore from the saved cache file within seconds.
