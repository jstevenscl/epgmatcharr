import asyncio
import logging
import httpx
from config import get_emby_config

logger = logging.getLogger(__name__)

_GUIDE_REFRESH_POLL_ATTEMPTS = 24   # up to 2 minutes
_GUIDE_REFRESH_POLL_DELAY_S  = 5.0


class EmbyClient:
    def __init__(self, url: str | None = None, api_key: str | None = None):
        if url is None or api_key is None:
            cfg = get_emby_config()
            url     = url     if url     is not None else cfg["url"]
            api_key = api_key if api_key is not None else cfg["api_key"]
        self._base    = url.rstrip("/")
        self._api_key = api_key

    def _params(self, extra: dict | None = None) -> dict:
        p = {"api_key": self._api_key}
        if extra:
            p.update(extra)
        return p

    async def get(self, path: str, params: dict | None = None):
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{self._base}{path}", params=self._params(params))
            if not r.is_success:
                logger.error("[EmbyClient] GET %s -> %d: %s", path, r.status_code, r.text[:500])
            r.raise_for_status()
            return r.json() if r.content else None

    async def post(self, path: str, data: dict):
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self._base}{path}", params=self._params(), json=data)
            if not r.is_success:
                logger.error("[EmbyClient] POST %s -> %d: %s", path, r.status_code, r.text[:500])
            r.raise_for_status()
            return r.json() if r.content else None

    async def delete(self, path: str, params: dict | None = None):
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.delete(f"{self._base}{path}", params=self._params(params))
            if not r.is_success:
                logger.error("[EmbyClient] DELETE %s -> %d: %s", path, r.status_code, r.text[:500])
            r.raise_for_status()
            return r.status_code

    # ── Connection ───────────────────────────────────────────────────────────

    async def test_connection(self) -> dict:
        try:
            info = await self.get("/emby/System/Info")
            return {
                "ok": True,
                "server_name": info.get("ServerName"),
                "version": info.get("Version"),
                "pending_restart": bool(info.get("HasPendingRestart")),
            }
        except httpx.ConnectError:
            return {"ok": False, "message": "Could not connect — check the URL"}
        except httpx.TimeoutException:
            return {"ok": False, "message": "Connection timed out"}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                return {"ok": False, "message": "Invalid API key"}
            return {"ok": False, "message": f"Unexpected response: HTTP {exc.response.status_code}"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    # ── Live TV channels ─────────────────────────────────────────────────────

    async def get_managed_channels(self) -> list[dict]:
        """All Live TV channels Emby has scanned from its tuners, with ManagementId
        (needed to push a mapping) and ListingsChannelId (the currently-mapped
        Gracenote station ID, if any)."""
        data = await self.get("/emby/LiveTv/Manage/Channels")
        return data.get("Items", []) if data else []

    # ── Listings providers (Gracenote lineups) ──────────────────────────────

    async def discover_lineups(self, zip_code: str, country: str = "US") -> list[dict]:
        """Lineups (OTA/cable/satellite/VMVPD) available for a ZIP, with no
        provider pre-configured. Returns [{Id, Name}, ...]."""
        return await self.get(
            "/emby/LiveTv/ListingProviders/Lineups",
            {"type": "embygn", "location": zip_code, "country": country},
        ) or []

    async def list_providers(self) -> list[dict]:
        return await self.get("/emby/LiveTv/ListingProviders") or []

    async def add_provider(self, listings_id: str, zip_code: str, country: str, name: str) -> dict:
        return await self.post("/emby/LiveTv/ListingProviders", {
            "Type": "embygn",
            "ListingsId": listings_id,
            "Country": country,
            "ZipCode": zip_code,
            "Name": name,
        })

    async def delete_provider(self, provider_id: str) -> None:
        await self.delete("/emby/LiveTv/ListingProviders", {"Id": provider_id})

    async def get_channel_mapping_options(self, provider_id: str) -> list[dict]:
        """Station candidates [{Id, Name}, ...] a given provider (lineup) carries."""
        data = await self.get("/emby/LiveTv/ChannelMappingOptions", {"providerId": provider_id})
        return data.get("ProviderChannels", []) if data else []

    async def push_channel_mapping(self, provider_id: str, tuner_channel_id: str, station_id: str) -> dict:
        return await self.post("/emby/LiveTv/ChannelMappings", {
            "ProviderId": provider_id,
            "TunerChannelId": tuner_channel_id,
            "ProviderChannelId": station_id,
        })

    _CACHED_IMAGE_TYPES = ("Primary", "LogoLight", "LogoLightColor")

    async def clear_channel_images(self, item_id: str) -> None:
        """Emby caches downloaded channel artwork (logo etc) independently of the
        listings mapping -- correcting or clearing a wrong mapping does NOT refresh
        or drop the image that was fetched under the old (wrong) mapping, so a
        channel can keep showing a stale/incorrect logo even after its guide data
        is fixed. Delete the cached image tags so Emby re-fetches fresh ones for
        whatever the channel is actually mapped to now (or none, if unmapped)."""
        async def _del(image_type: str):
            try:
                await self.delete(f"/emby/Items/{item_id}/Images/{image_type}")
            except Exception:
                pass  # fine if this particular type was never cached
        for image_type in self._CACHED_IMAGE_TYPES:
            await _del(image_type)

    async def clear_channel_mapping(self, provider_id: str, tuner_channel_id: str) -> dict:
        """Removes whatever guide mapping a channel currently has (explicit or
        Emby's own auto-match). provider_id just needs to be any currently-valid
        provider id -- Emby clears the mapping regardless of which one it is."""
        return await self.post("/emby/LiveTv/ChannelMappings", {
            "ProviderId": provider_id,
            "TunerChannelId": tuner_channel_id,
            "ProviderChannelId": "",
        })

    # ── Tuner hosts ──────────────────────────────────────────────────────────

    async def list_tuner_hosts(self) -> list[dict]:
        return await self.get("/emby/LiveTv/TunerHosts") or []

    async def disable_auto_match_by_number(self) -> list[str]:
        """Emby's 'AllowMappingByNumber' silently auto-matches any unmapped channel
        to whatever the active listings provider calls that same channel NUMBER --
        completely independent of any explicit ChannelMappings call. Since channel
        numbers are provider-arbitrary (a coincidental number match doesn't mean the
        same station), this corrupts channels EPGmatcharr deliberately left unmapped
        the moment ANY provider becomes active. Disables it on every tuner that has
        it on. Returns the names of tuners that were changed."""
        changed: list[str] = []
        for tuner in await self.list_tuner_hosts():
            if not tuner.get("AllowMappingByNumber"):
                continue
            tuner["AllowMappingByNumber"] = False
            await self.post("/emby/LiveTv/TunerHosts", tuner)
            changed.append(tuner.get("FriendlyName") or tuner.get("Type") or tuner["Id"])
        return changed

    # ── Guide refresh ────────────────────────────────────────────────────────

    async def refresh_guide(self, wait: bool = True) -> bool:
        """Deleting a channel's cached images (clear_channel_images) only drops the
        stale artwork -- Emby doesn't lazily re-fetch a replacement on next view
        (confirmed: a direct image request 404s until this runs). Emby's own
        "Refresh Guide" scheduled task (Live TV category) is what re-fetches
        artwork matching each channel's current mapping. Returns False if the task
        couldn't be found (older/different Emby versions may name it differently)."""
        tasks = await self.get("/emby/ScheduledTasks") or []
        task = next((t for t in tasks if t.get("Category") == "Live TV" and t.get("Name") == "Refresh Guide"), None)
        if not task:
            logger.warning("[EmbyClient] 'Refresh Guide' scheduled task not found -- skipping")
            return False
        await self.post(f"/emby/ScheduledTasks/Running/{task['Id']}", {})
        if not wait:
            return True
        for _ in range(_GUIDE_REFRESH_POLL_ATTEMPTS):
            await asyncio.sleep(_GUIDE_REFRESH_POLL_DELAY_S)
            status = await self.get(f"/emby/ScheduledTasks/{task['Id']}")
            if status and status.get("State") == "Idle":
                return True
        logger.warning("[EmbyClient] 'Refresh Guide' didn't finish within the poll window")
        return True
