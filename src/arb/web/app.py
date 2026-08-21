"""FastAPI application for the Arbitrage Trading Console.

Serves the static single-page frontend and the REST API in ``api.py``.

The frontend is five tabs over one router. There is no second router: every
figure the console shows is computed server-side in ``api.py`` and rendered
verbatim, so the display cannot become a second opinion on the ranking or the
margins.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, api_engine

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Arbitrage Trading Console", version="0.1.0")

app.include_router(api.router)
app.include_router(api_engine.router)


static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static_files")
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def create_app() -> FastAPI:
    """Return the FastAPI application instance."""
    return app
