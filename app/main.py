"""FastAPI application factory.

Serves the JSON API under ``/api`` and the single-page web panel from ``web/`` at
the root. The download engine and Telegram clients start/stop with the app
lifespan.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import WebConfig
from app.core.services import Services
from app.api import auth, downloads, files, settings as settings_api, telegram, ws

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))


def _render_index() -> str:
    """index.html with the version stamped into its asset URLs (cache-busting)."""
    try:
        with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
            return f.read().replace("__V__", __version__)
    except OSError:
        return "<!doctype html><title>TelegramMediaManager</title><p>web assets missing</p>"


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = Services()
    app.state.services = services
    await services.startup()
    try:
        yield
    finally:
        await services.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="TelegramMediaManager", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    for module in (auth, telegram, downloads, files, settings_api):
        app.include_router(module.router)
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    async def index():
        # Tiny entry document; revalidated each load so a new version's asset
        # URLs are picked up immediately.
        return HTMLResponse(_render_index(), headers={"Cache-Control": "no-cache"})

    # Versioned assets (…?v=X) never change for a given version, so cache them
    # long-term — the browser makes 0 requests for them until the version bumps.
    # Only the small index.html above is fetched (and 304'd) on each load.
    @app.middleware("http")
    async def _asset_cache(request, call_next):
        response = await call_next(request)
        if request.url.path.endswith((".js", ".css")):
            if "v=" in (request.url.query or ""):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache"
        return response

    # SPA assets (js / css / icon). "/" is handled by the route above.
    if os.path.isdir(WEB_DIR):
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()


def run():
    import uvicorn
    uvicorn.run(app, host=WebConfig.HOST, port=WebConfig.PORT, log_level="info")


if __name__ == "__main__":
    run()
