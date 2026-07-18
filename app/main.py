"""FastAPI application factory.

Serves the JSON API under ``/api`` and the single-page web panel from ``web/`` at
the root. The download engine and Telegram clients start/stop with the app
lifespan.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import WebConfig
from app.core.services import Services
from app.api import auth, downloads, files, settings as settings_api, telegram, ws

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))


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

    # SPA + assets. html=True serves index.html at "/".
    if os.path.isdir(WEB_DIR):
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()


def run():
    import uvicorn
    uvicorn.run(app, host=WebConfig.HOST, port=WebConfig.PORT, log_level="info")


if __name__ == "__main__":
    run()
