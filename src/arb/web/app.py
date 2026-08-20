"""FastAPI application for the Arbitrage Dashboard.

Serves the static frontend and REST API endpoints.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, api_engine

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Arbitrage Dashboard", version="0.1.0")

# Include API routers
app.include_router(api.router)
app.include_router(api_engine.router)

# Mount static files
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static_files")
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def create_app() -> FastAPI:
    """Return the FastAPI application instance."""
    return app
