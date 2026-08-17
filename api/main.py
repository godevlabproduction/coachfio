from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from adapters.base.registry import load_builtin_adapters, registry
from api.routes import account, auth, games, matches, site, social, usage
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
    # Fail loudly at boot, not after someone has uploaded a whole match. The
    # usual cause is `docker compose restart` after editing .env - restart reuses
    # the container's original environment, so the new key never arrives.
    if not get_settings().openai_api_key:
        log.error(
            "OPENAI_API_KEY is empty - native video analysis WILL fail. If you just "
            "edited .env, run: docker compose up -d --force-recreate api worker"
        )
    # The session cookie is signed with SECRET_KEY. Shipping the default would
    # let anyone mint a session for any account, so refuse to boot in a
    # deployment that has clearly gone live (secure cookies = behind TLS) while
    # still carrying it. Locally it is only a warning.
    _s = get_settings()
    if _s.secret_key == "dev-only-change-me":
        if _s.session_cookie_secure:
            raise RuntimeError(
                "SECRET_KEY is still the development default while session cookies are "
                "marked Secure. Set a real SECRET_KEY (e.g. `openssl rand -hex 32`) "
                "before serving over HTTPS - sessions are forgeable until you do."
            )
        log.warning("SECRET_KEY is the development default - fine locally, never in production.")
    yield


app = FastAPI(title="Coach.io - Gameplay Analysis (Phase 0)", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(account.router)
app.include_router(account.users_router)
app.include_router(auth.router)
app.include_router(games.router)
app.include_router(matches.router)
app.include_router(site.router)
app.include_router(social.router)
app.include_router(usage.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "adapters": registry.keys()}


# Serve the static frontend from the API itself, so ONE exposed port gives both
# the UI and the API (same-origin → no CORS, trivial to tunnel or put behind one
# proxy). Mounted LAST so it only catches paths the API routes didn't handle.
class _NoCacheStatic(StaticFiles):
    """Serve the frontend with caching switched off.

    There is no build step and no content hash in the filenames, so `coach.js`
    keeps one URL forever. Served with only an ETag, browsers happily reuse their
    copy, and an edit appears to have done nothing until someone remembers to
    hard-refresh. That has burned real debugging time more than once - twice on a
    change that was already correct on disk and already being served - so the
    default is now "always fresh" and the papercut is gone rather than documented.

    This is the dev posture. Behind a CDN, fingerprint the assets and cache them
    hard instead.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"


# The root domain gets a different home page from a game site. Registered before
# the static mount so it wins for "/" only; every other path still falls through
# to the shared frontend, which is what keeps this one deployment rather than two.
# The ROOT DOMAIN is the hub and has its own pages under frontend/root/. A game
# host gets the app's own file for the same path. Registered before the static
# mount so these three paths are host-aware and everything else falls through,
# which is what keeps this one deployment rather than two.
_HUB_PAGES = {
    "/": ("root/index.html", "index.html"),
    "/signin/": ("root/signin/index.html", "signin/index.html"),
    # /hub/ is hub-only: a game site has no game picker.
    "/hub/": ("root/hub/index.html", None),
}


def _serve(rel: str):
    resp = FileResponse(_frontend_dir / rel)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def _hub_or_app(request: Request, path: str):
    from fastapi import HTTPException

    from api.routes.site import resolve_game

    hub_file, app_file = _HUB_PAGES[path]
    if resolve_game(request) is None:
        return _serve(hub_file)
    if app_file is None:
        raise HTTPException(404, "not found on a game site")
    return _serve(app_file)


@app.get("/", include_in_schema=False)
def home(request: Request):
    return _hub_or_app(request, "/")


@app.get("/signin/", include_in_schema=False)
def signin(request: Request):
    return _hub_or_app(request, "/signin/")


@app.get("/hub/", include_in_schema=False)
def game_hub(request: Request):
    return _hub_or_app(request, "/hub/")


if _frontend_dir.is_dir():
    app.mount("/", _NoCacheStatic(directory=str(_frontend_dir), html=True), name="frontend")
