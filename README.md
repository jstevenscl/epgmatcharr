# EPGmatcharr

A standalone Docker container that matches your Dispatcharr channels to EPG sources automatically.

Connect EPGmatcharr to your Dispatcharr instance, run a match, review the results, and commit — channel EPG assignments update instantly without touching Dispatcharr directly.

![Match results showing High and Review confidence badges](docs/screenshots/ug-18-match-results.png)

---

## Features

- **Automatic EPG matching** — tiered matching engine (tvg_id exact → GN exact → GN fwd/rev → GN DB bridge → callsign → fuzzy name) with confidence scoring
- **High / Review confidence badges** — High-confidence matches are auto-selected; Review matches let you pick from ranked candidates
- **Multi-source support** — match against any EPG source configured in Dispatcharr; filter by TVG-ID pattern
- **Bulk workflows** — load all unassigned channels, or filter to channels from a specific existing EPG source to re-match them
- **GN Station Matcher** — dedicated tab to assign Gracenote station IDs (`tvc_guide_stationid`) directly to channels; scored candidates, staged commit, country filter (US/GB/DE/NL and more), a sticky channel group filter that's remembered across sessions, and a clear button to remove bad mappings
- **Recheck Existing Matches** — re-scans channels that already have a GN station ID and flags ones that are now known-stale (e.g. a bare call sign where a `-DT`/`-CD`/`-LD` entry exists), with corrected suggestions ready to commit
- **GN Station DB** — weekly-updated SQLite database of Gracenote station IDs and call signs; enables bridge matching between call-sign channels and Gracenote EPG sources
- **Emby Guide Sync** — automatically configures Emby's built-in Gracenote (embygn) guide provider and maps your channels to the correct station IDs, using the GN station IDs already assigned in EPGmatcharr; auto-detects the ZIP codes/markets needed straight from your channels' call signs, so no manual market lookup is required
- **Emby Sync manual overrides** — search-and-map or clear any single Emby channel directly, for the channels automatic lineup discovery can't resolve
- **Emby Sync respect-existing option** — leave channels that already have a different mapping in Emby untouched instead of overwriting them
- **Emby Sync excluded channel groups** — permanently skip specific channel groups (e.g. SiriusXM) from ever being pushed to Emby, even if they have a GN station ID
- **Force Emby Refresh** — trigger Emby's own guide refresh directly from Settings, without needing GN station IDs or a full Preview/Push run first
- **Prefer CALLSIGN-DT** — optional tiebreaker in EPG Matcher that favors `-DT` callsign variants over bare callsigns; recommended when matching against Gracenote EPG sources
- **Backfill on commit** — optionally write matched GN station IDs or tvg-ids back to Dispatcharr channels at commit time
- **EPG Sources ordered by priority** — the EPG source picker follows the priority order you've already set for each provider in Dispatcharr
- **EPG Guide** — live programme grid; can be disabled in Settings for a lighter experience
- **Now Playing** — shows the current program from the EPG cache for each matched channel
- **Stream preview** — built-in video player for HLS and MPEG-TS streams directly from Dispatcharr
- **EPG cache warming** — downloads and indexes EPG sources in the background with per-source status; sources disabled in Dispatcharr are skipped, and the largest epg.guru XMLTV feeds are served from a pre-parsed cache instead of being parsed locally
- **Inline channel renaming** — edit channel names during the match flow; names commit alongside EPG assignments
- **Themes** — Dark, Mid, Light, and Mono

---

## Quick Start

### 1. Add to your Docker Compose stack

```yaml
services:
  epgmatcharr:
    image: ghcr.io/jstevenscl/epgmatcharr:latest
    container_name: epgmatcharr
    restart: unless-stopped
    ports:
      - "8281:8281"
    volumes:
      - epgmatcharr_data:/app/data

volumes:
  epgmatcharr_data:
```

```bash
docker compose up -d
```

Open **http://your-server:8281** in a browser.

### 2. Configure

On first launch you will see the setup screen. Enter your Dispatcharr URL and API token, then click **Test Connection**. Once confirmed, click **Connect**.

See the [User Guide](docs/USERGUIDE.md) for the full setup walkthrough with screenshots.

---

## Building from Source

```bash
git clone https://github.com/jstevenscl/epgmatcharr.git
cd epgmatcharr
docker build -t epgmatcharr:latest .
```

---

## Environment Variables

All configuration can be done through the web UI. The following environment variables are optional overrides:

| Variable | Description |
|---|---|
| `DISPATCHARR_URL` | Dispatcharr base URL (e.g. `http://192.168.1.100:9191`) |
| `DISPATCHARR_TOKEN` | Dispatcharr API token |
| `EMBY_URL` | Emby server base URL (e.g. `http://192.168.1.100:8096`) |
| `EMBY_API_KEY` | Emby API key |

If set, these take priority over anything saved through the UI. `EMBY_URL`/`EMBY_API_KEY` must both be set to take effect; ZIP code and country remain configurable through the UI either way.

---

## Data Persistence

EPGmatcharr stores all configuration and the EPG cache in the named volume `epgmatcharr_data` (mounted at `/app/data` inside the container). This includes:

- `config.json` — Dispatcharr URL, token, EPG settings, credentials
- `epg_cache.json` — cached EPG data (rebuilt automatically on startup and on TTL expiry)
- `sessions.json` — active login sessions

The EPG cache survives container restarts. To force a full re-download, delete `epg_cache.json` from the volume before restarting.

A bundled `fcc_market_db.sqlite` (built from public FCC station license data and GeoNames postal data) ships in the image itself, not the data volume — it's static reference data used to auto-detect ZIP codes for Emby Sync, not user data.

---

## Emby Guide Sync

If you run Emby with its built-in Gracenote (embygn) Live TV guide, EPGmatcharr can configure it for you automatically:

1. Assign GN station IDs to your channels first, using the **GN Matcher** tab.
2. In **Settings**, enter your Emby server URL and API key (found in Emby under Dashboard → Advanced → API Keys), then **Test Connection** and **Save**. ZIP code and country are optional — EPGmatcharr auto-detects the markets it needs from your channels' call signs.
3. Open the **Emby Sync** tab and click **Preview Coverage** to see what would be mapped, with no changes made to Emby.
4. Click **Push** to write the mappings — EPGmatcharr adds the minimal set of Gracenote lineups needed, maps each channel to its known station ID, disables Emby's number-based auto-matching, and corrects anything Emby's own background matching changes afterward.

For channels the automatic flow can't resolve, use the search icon on any row to manually map that one channel, or the trash icon to clear its mapping — both apply immediately to Emby. The **excluded channel groups** panel lets you permanently skip an entire group (e.g. SiriusXM) from Emby Sync, and the **respect-existing** checkbox leaves channels with a different existing mapping untouched instead of overwriting them.

Need a guide refresh without running a full sync? The **Force Emby Refresh** button in Settings triggers Emby's own guide refresh task directly — no GN station IDs or Preview/Push required. You only need to enter your Emby API key once; Test Connection and Save both reuse the saved key if you leave the field blank later.

See the [User Guide](docs/USERGUIDE.md#12-emby-guide-sync) for the full walkthrough.

---

## EPG Cache Settings

Configurable via **Settings → EPG Cache** in the UI:

| Setting | Default | Description |
|---|---|---|
| TTL | 1 hour | How long before the cache is refreshed |
| Window | 7 days | How many days of EPG data to download |

---

## Authentication

EPGmatcharr supports an optional login password. Once credentials are set in Settings, all pages require login. Sessions persist until explicitly logged out.

If no credentials are configured, the app is open to anyone who can reach port 8281 — restrict access via your firewall or reverse proxy if needed.

---

## Requirements

- Docker with Compose
- A running [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr) instance
- Dispatcharr API token (Settings → API in Dispatcharr)
- EPG sources configured in Dispatcharr

---

## User Guide

See **[docs/USERGUIDE.md](docs/USERGUIDE.md)** for a complete walkthrough of both matching workflows with screenshots.
