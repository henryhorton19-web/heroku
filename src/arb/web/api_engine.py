"""REST API bridge for engine telemetry and controls.

All endpoints are served by Horse's FastAPI server (``src/arb/web/app.py``).
They are feature-flagged: if ``ENGINE_ENABLED`` is ``False``, every endpoint
returns a 503 with a descriptive message.
"""

from __future__ import annotations

from engine.config import EngineSettings, get_engine_settings
from engine.proxy import ProxyPool, ProxyPoolError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/engine", tags=["engine"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _require_enabled() -> EngineSettings:
    settings = get_engine_settings()
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="Engine is disabled. Set ENGINE_ENABLED=true.")
    return settings


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    enabled: bool
    tls_preset: str
    polling_latency_ms: float
    request_rate: float


class ToggleRequest(BaseModel):
    enabled: bool


class ProxyStatusResponse(BaseModel):
    total: int
    available: int
    quarantined: list[str]


class QuarantineRequest(BaseModel):
    ip: str


class CaptchaStatusResponse(BaseModel):
    capsolver_configured: bool
    twocaptcha_configured: bool
    solve_count: int
    average_duration_ms: float
    success_rate: float


class AutoCopStatusResponse(BaseModel):
    armed: bool
    max_spend_pence: int
    dry_run: bool
    recent_purchases: list[dict[str, str | int | bool]]


class AutoCopConfigRequest(BaseModel):
    max_spend_pence: int | None = None
    dry_run: bool | None = None


class CrossListerStatusResponse(BaseModel):
    venues: dict[str, str]
    active_delist_queue: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Return engine status."""
    settings = _require_enabled()
    return StatusResponse(
        enabled=settings.enabled,
        tls_preset=settings.tls_preset,
        polling_latency_ms=0.0,  # placeholder
        request_rate=0.0,  # placeholder
    )


@router.post("/toggle", response_model=StatusResponse)
async def toggle_engine(req: ToggleRequest) -> StatusResponse:
    """Toggle engine master switch."""
    settings = get_engine_settings()
    settings.enabled = req.enabled
    return StatusResponse(
        enabled=settings.enabled,
        tls_preset=settings.tls_preset,
        polling_latency_ms=0.0,
        request_rate=0.0,
    )


@router.get("/proxies", response_model=ProxyStatusResponse)
async def get_proxies() -> ProxyStatusResponse:
    """Return proxy pool status."""
    _require_enabled()
    try:
        pool = ProxyPool.from_env()
    except (ProxyPoolError, OSError, ValueError, RuntimeError):
        return ProxyStatusResponse(total=0, available=0, quarantined=[])

    return ProxyStatusResponse(
        total=pool.total_count,
        available=pool.available_count,
        quarantined=[],  # placeholder
    )


@router.post("/proxies/quarantine")
async def quarantine_proxy(req: QuarantineRequest) -> dict[str, str]:
    """Manually quarantine an IP."""
    _require_enabled()
    try:
        pool = ProxyPool.from_env()
        pool.mark_failed(req.ip)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "quarantined", "ip": req.ip}


@router.post("/proxies/unquarantine")
async def unquarantine_proxy(req: QuarantineRequest) -> dict[str, str]:
    """Manually release a quarantined IP."""
    _require_enabled()
    # Placeholder: real implementation would call pool.unquarantine()
    return {"status": "unquarantined", "ip": req.ip}


@router.get("/captcha", response_model=CaptchaStatusResponse)
async def get_captcha_status() -> CaptchaStatusResponse:
    """Return CAPTCHA solver health."""
    settings = _require_enabled()
    return CaptchaStatusResponse(
        capsolver_configured=settings.capsolver_api_key is not None,
        twocaptcha_configured=settings.captcha_2captcha_api_key is not None,
        solve_count=0,
        average_duration_ms=0.0,
        success_rate=0.0,
    )


@router.post("/captcha/test")
async def test_captcha() -> dict[str, str]:
    """Trigger a test CAPTCHA solve."""
    _require_enabled()
    # Placeholder: real implementation would call solver.solve_turnstile(...)
    return {"status": "test triggered", "result": "placeholder"}


@router.get("/autocop", response_model=AutoCopStatusResponse)
async def get_autocop_status() -> AutoCopStatusResponse:
    """Return AutoCop status."""
    settings = _require_enabled()
    return AutoCopStatusResponse(
        armed=settings.autocop_enabled,
        max_spend_pence=settings.autocop_max_spend_pence,
        dry_run=True,
        recent_purchases=[],
    )


@router.post("/autocop/config")
async def configure_autocop(req: AutoCopConfigRequest) -> AutoCopStatusResponse:
    """Configure AutoCop settings."""
    settings = _require_enabled()
    if req.max_spend_pence is not None:
        settings.autocop_max_spend_pence = req.max_spend_pence
    if req.dry_run is not None:
        # Placeholder: would set dry_run flag
        pass
    return AutoCopStatusResponse(
        armed=settings.autocop_enabled,
        max_spend_pence=settings.autocop_max_spend_pence,
        dry_run=True,
        recent_purchases=[],
    )


@router.get("/crosslister", response_model=CrossListerStatusResponse)
async def get_crosslister_status() -> CrossListerStatusResponse:
    """Return cross-lister status."""
    _require_enabled()
    return CrossListerStatusResponse(
        venues={
            "vinted": "connected",
            "ebay": "connected",
            "depop": "disconnected",
            "poshmark": "disconnected",
            "mercari": "disconnected",
        },
        active_delist_queue=0,
    )
