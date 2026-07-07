import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import get_epg_settings, is_configured
from dispatcharr_client import DispatcharrClient
from epg_cache import warm_cache
import log_buffer
from routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log_buffer.install()

logger    = logging.getLogger("epgmatcharr")
STATIC_DIR = Path(__file__).parent / "static"


async def _epg_warmer() -> None:
    """Background task: warm EPG cache at startup and re-warm before each TTL expiry."""
    # Brief pause to let uvicorn finish binding before firing HTTP requests
    await asyncio.sleep(3)
    while True:
        if is_configured():
            try:
                client      = DispatcharrClient()
                sources_raw = await client.get("/api/epg/sources/")
                sources     = sources_raw if isinstance(sources_raw, list) else sources_raw.get("results", [])
                url_map     = {s["id"]: s["url"]  for s in sources if s.get("url")}
                name_map    = {s["id"]: s.get("name", f"Source {s['id']}") for s in sources if s.get("url")}
                if url_map:
                    logger.info("[bg_warmer] warming %d EPG source(s)…", len(url_map))
                    await warm_cache(url_map, name_map)
                    logger.info("[bg_warmer] EPG cache warm complete")
            except Exception as exc:
                logger.warning("[bg_warmer] warm failed: %s", exc)

        ttl_hours   = get_epg_settings().get("epg_cache_ttl_hours", 1.0)
        sleep_secs  = max(300, int(ttl_hours * 3600))
        logger.info("[bg_warmer] next warm in %.0f min", sleep_secs / 60)
        await asyncio.sleep(sleep_secs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("EPGmatcharr started")
    task = asyncio.create_task(_epg_warmer())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="EPGmatcharr", version="0.3.00", lifespan=lifespan)
app.include_router(router)

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        return FileResponse(str(STATIC_DIR / "index.html"))
