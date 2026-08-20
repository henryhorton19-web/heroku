"""The ASGI application. Local-only by default, and that is a security decision.

`arb web` binds to `127.0.0.1`. This console exposes the full ledger, every purchase
decision and the tax position, and it has **no authentication** — because adding auth
to a single-user local tool is machinery that protects nothing while implying it
protects something. The protection is the bind address, so the default must be the
loopback and a wider bind must be a deliberate act with a warning attached.

CORS is not enabled. The page is served from the same origin as the API, so
cross-origin access would exist only to let something else read your books.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from arb import __version__
from arb.web.api import router

__all__ = ["STATIC_DIR", "create_app"]

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Build the application. A factory so tests get a fresh instance per case."""
    app = FastAPI(
        title="arb console",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
