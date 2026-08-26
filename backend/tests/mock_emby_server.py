"""
Mock Emby server -- lets emby_sync.py / emby_client.py be exercised against
something that behaves like a real Emby's Live TV API, without an actual Emby
installation or a Premiere subscription for Gracenote listings.

Implements just the endpoints emby_client.py calls (see that file), backed by
simple in-memory state. Auth is not checked -- any api_key query param is
accepted, since this only exists for local testing.

Standalone use -- point a real, running EPGmatcharr backend at it and click
through the actual Emby Sync UI:

    pip install fastapi uvicorn
    python mock_emby_server.py                       # serves on http://127.0.0.1:8096
    curl -X POST http://127.0.0.1:8096/mock/seed -H "Content-Type: application/json" -d @scenario.json
    # then set EMBY_URL=http://127.0.0.1:8096 and EMBY_API_KEY=anything

Automated use -- see conftest.py / test_emby_sync.py, which start a fresh
instance of this app in-process on a free port for each test and seed it
directly via `mock_emby.state[...]` (no HTTP round-trip needed).
"""

from __future__ import annotations

import itertools

from fastapi import FastAPI, HTTPException, Request


def create_app() -> FastAPI:
    """Fresh app + fresh in-memory state, so concurrent tests never share data."""
    app = FastAPI(title="Mock Emby Server")

    state: dict = {
        "tuner_hosts":     [],   # [{Id, FriendlyName, Type, DeviceId, Url, AllowMappingByNumber}, ...]
        "channels":        [],   # [{Id, Name, ChannelNumber, ManagementId, ListingsChannelId}, ...]
        "lineups":         {},   # {(zip_code, country): [{Id, Name}, ...]}
        "lineup_stations": {},   # {listings_id: [{Id, Name}, ...]}
        "providers":       {},   # {provider_id: {Id, Type, ListingsId, Country, ZipCode, Name}}
        "scheduled_tasks": [
            {"Id": "refresh-guide-task", "Category": "Live TV", "Name": "Refresh Guide", "State": "Idle"},
        ],
        "calls": [],   # every request this app has served, for test assertions
    }
    provider_seq = itertools.count(1)
    app.state.mock = state

    @app.middleware("http")
    async def _log_calls(request: Request, call_next):
        response = await call_next(request)
        state["calls"].append({"method": request.method, "path": request.url.path})
        return response

    # ── Connection ───────────────────────────────────────────────────────────

    @app.get("/emby/System/Info")
    async def system_info():
        return {"ServerName": "Mock Emby", "Version": "4.9.0.0", "HasPendingRestart": False}

    # ── Live TV channels ─────────────────────────────────────────────────────

    @app.get("/emby/LiveTv/Manage/Channels")
    async def get_channels():
        return {"Items": state["channels"]}

    # ── Listings providers (Gracenote lineups) ──────────────────────────────

    @app.get("/emby/LiveTv/ListingProviders/Lineups")
    async def discover_lineups(type: str = "embygn", location: str = "", country: str = "US"):
        return state["lineups"].get((location, country), [])

    @app.get("/emby/LiveTv/ListingProviders")
    async def list_providers():
        return list(state["providers"].values())

    @app.post("/emby/LiveTv/ListingProviders")
    async def add_provider(request: Request):
        body = await request.json()
        pid = f"provider-{next(provider_seq)}"
        provider = {
            "Id":         pid,
            "Type":       body.get("Type", "embygn"),
            "ListingsId": body["ListingsId"],
            "Country":    body.get("Country", "US"),
            "ZipCode":    body.get("ZipCode", ""),
            "Name":       body.get("Name", ""),
        }
        state["providers"][pid] = provider
        return provider

    @app.delete("/emby/LiveTv/ListingProviders")
    async def delete_provider(Id: str):
        state["providers"].pop(Id, None)
        return {}

    @app.get("/emby/LiveTv/ChannelMappingOptions")
    async def channel_mapping_options(providerId: str):
        provider = state["providers"].get(providerId)
        if not provider:
            raise HTTPException(404, "unknown provider")
        return {"ProviderChannels": state["lineup_stations"].get(provider["ListingsId"], [])}

    @app.post("/emby/LiveTv/ChannelMappings")
    async def push_channel_mapping(request: Request):
        body = await request.json()
        tuner_channel_id = body["TunerChannelId"]
        station_id = body.get("ProviderChannelId") or None
        for ch in state["channels"]:
            if ch.get("ManagementId") == tuner_channel_id:
                ch["ListingsChannelId"] = station_id
                break
        return {}

    # ── Tuner hosts ──────────────────────────────────────────────────────────

    @app.get("/emby/LiveTv/TunerHosts")
    async def tuner_hosts():
        return state["tuner_hosts"]

    @app.post("/emby/LiveTv/TunerHosts")
    async def update_tuner_host(request: Request):
        body = await request.json()
        for t in state["tuner_hosts"]:
            if t.get("Id") == body.get("Id"):
                t.update(body)
                break
        return body

    # ── Guide refresh ────────────────────────────────────────────────────────

    @app.get("/emby/ScheduledTasks")
    async def scheduled_tasks():
        return state["scheduled_tasks"]

    @app.post("/emby/ScheduledTasks/Running/{task_id}")
    async def run_task(task_id: str):
        return {}

    @app.get("/emby/ScheduledTasks/{task_id}")
    async def task_status(task_id: str):
        task = next((t for t in state["scheduled_tasks"] if t["Id"] == task_id), None)
        if not task:
            raise HTTPException(404, "unknown task")
        return task

    @app.delete("/emby/Items/{item_id}/Images/{image_type}")
    async def clear_image(item_id: str, image_type: str):
        return {}

    # ── Test-only control surface ────────────────────────────────────────────
    # Not part of the real Emby API -- lets a standalone curl/manual session
    # load a scenario the same way conftest.py does in-process.

    @app.post("/mock/reset")
    async def mock_reset():
        state.update({
            "tuner_hosts": [], "channels": [], "lineups": {}, "lineup_stations": {},
            "providers": {}, "calls": [],
        })
        return {"ok": True}

    @app.post("/mock/seed")
    async def mock_seed(request: Request):
        """Body: {tuner_hosts, channels, lineup_stations, lineups: {"zip|country": [...]}}"""
        body = await request.json()
        if "tuner_hosts" in body:
            state["tuner_hosts"] = body["tuner_hosts"]
        if "channels" in body:
            state["channels"] = body["channels"]
        if "lineup_stations" in body:
            state["lineup_stations"] = body["lineup_stations"]
        if "lineups" in body:
            state["lineups"] = {tuple(k.split("|", 1)): v for k, v in body["lineups"].items()}
        return {"ok": True}

    @app.get("/mock/state")
    async def mock_state():
        return {
            "tuner_hosts": state["tuner_hosts"],
            "channels":    state["channels"],
            "providers":   state["providers"],
            "calls":       state["calls"][-50:],
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8096)
