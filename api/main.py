from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from adapters.base.registry import load_builtin_adapters, registry
from api.routes import games, matches, usage
from core.config import get_settings
from core.storage.db import init_db
from core.storage.objectstore import get_object_store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("coachio.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_builtin_adapters()
    init_db()
    try:
        get_object_store().ensure_bucket()
    except Exception as exc:  # noqa: BLE001
        log.warning("object store not ready at startup: %s", exc)
    log.info("adapters loaded: %s", registry.keys())
    yield


app = FastAPI(title="Coach.io — Gameplay Analysis (Phase 0)", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(games.router)
app.include_router(matches.router)
app.include_router(usage.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "adapters": registry.keys()}


# Serve the static frontend from the API itself, so ONE exposed port gives both
# the UI and the API (same-origin → no CORS, trivial to tunnel or put behind one
# proxy). Mounted LAST so it only catches paths the API routes didn't handle.
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
