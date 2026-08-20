"""Standalone CLI entrypoint for the engine.

Usage::

    uv run python -m engine.cli monitor --keyword "nike air max"
    uv run python -m engine.cli monitor --keyword "adidas" --max-price 25.00
"""

from __future__ import annotations

import asyncio

import typer

from engine.config import get_engine_settings
from engine.monitor import MonitorConfig, run_monitor_loop
from engine.proxy import ProxyPool
from engine.tls import TlsSession

# ---------------------------------------------------------------------------
# CLI app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="engine",
    help="High-throughput reselling & arbitrage engine.",
    no_args_is_help=True,
)


@app.command()
def monitor(
    keyword: str = typer.Option(..., "--keyword", "-k", help="Search keyword."),
    max_price: float | None = typer.Option(None, "--max-price", "-p", help="Maximum price in GBP."),
    interval: int = typer.Option(5, "--interval", "-i", help="Polling interval in seconds."),
) -> None:
    """Run the Vinted monitor worker."""
    settings = get_engine_settings()
    if not settings.enabled:
        typer.echo(
            "Engine is disabled. Set ENGINE_ENABLED=true in your environment or .env file.",
            err=True,
        )
        raise typer.Exit(1)

    # Build config
    config = MonitorConfig(
        keyword=keyword,
        max_price_pence=int(round(max_price * 100)) if max_price is not None else None,
        poll_interval_seconds=float(interval),
    )

    # Build dependencies
    tls_session = TlsSession(preset=settings.tls_preset)
    try:
        proxy_pool = ProxyPool.from_env()
    except Exception as exc:
        typer.echo(f"Failed to load proxy pool: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"Starting monitor for {keyword!r} (max price: {max_price} GBP, interval: {interval}s)"
    )

    try:
        asyncio.run(run_monitor_loop(config, tls_session, proxy_pool))
    except KeyboardInterrupt:
        typer.echo("\nMonitor stopped by user.")
    finally:
        tls_session.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
