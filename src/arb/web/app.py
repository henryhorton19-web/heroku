"""FastAPI application for the Arbitrage Dashboard.

Serves the static frontend and REST API endpoints.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api_engine

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Arbitrage Dashboard", version="0.1.0")

# Mount static files
static_dir = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

# Include engine API router
app.include_router(api_engine.router)
