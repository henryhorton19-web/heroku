"""`arb` command line. Everything in this build is run by hand from here.

There is no scheduler and no daemon. That is deliberate: monitors and alerting are
deferred, and the scanner is a pure function precisely so that adding a scheduler
later is additive rather than a rewrite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from arb import __version__
from arb.config import Settings, get_settings
from arb.db import VintedRef
from arb.refdata import load_reference_data
from arb.store import alembic_config, make_engine, session_scope, upgrade_to_head

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
db_app = typer.Typer(no_args_is_help=True, help="Schema management.")
app.add_typer(db_app, name="db")

__all__ = ["app", "db_app"]


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply all pending migrations."""
    settings = get_settings()
    upgrade_to_head(settings.db_url)
    typer.echo(f"schema at head: {settings.db_path}")


@db_app.command("current")
def db_current() -> None:
    """Show the applied revision."""
    command.current(alembic_config(get_settings().db_url), verbose=False)


@app.command("load-refdata")
def load_refdata(
    data_dir: Annotated[
        Path | None,
        typer.Option(help="Directory holding brand.json, catalog.json, etc."),
    ] = None,
) -> None:
    """Load the Vinted ID tables into `vinted_ref`.

    Clone `0AlphaZero0/Vinted-data` and point this at its DATA directory. The data
    is a FR-locale capture, so titles are advisory and joins key on id or code.
    """
    settings = get_settings()
    target = data_dir if data_dir is not None else settings.data_dir
    if not target.is_dir():
        typer.echo(f"no such directory: {target}", err=True)
        raise typer.Exit(code=2)

    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        counts = load_reference_data(session, target)

    if not counts:
        typer.echo(f"no reference files found in {target}", err=True)
        raise typer.Exit(code=1)
    for kind in sorted(counts):
        typer.echo(f"{kind:<12} {counts[kind]:>6}")


@app.command()
def doctor() -> None:
    """Check the environment: database, migrations, reference data, credentials.

    Reports rather than fixes. Exits non-zero only if the database is unusable,
    since missing credentials just mean a later step is unavailable.
    """
    settings = get_settings()
    lines: list[str] = [f"db          {settings.db_path}"]

    engine = make_engine(settings.db_url)
    try:
        with session_scope(engine) as session:
            ref_total = session.scalar(select(func.count()).select_from(VintedRef)) or 0
            brands = (
                session.scalar(
                    select(func.count()).select_from(VintedRef).where(VintedRef.kind == "brand")
                )
                or 0
            )
    except SQLAlchemyError as exc:
        typer.echo(f"db          UNUSABLE: {exc}", err=True)
        raise typer.Exit(code=1) from None

    lines.append(f"refdata     {ref_total} rows ({brands} brands)")
    if ref_total == 0:
        lines.append("            -> run `arb load-refdata --data-dir <Vinted-data/DATA>`")

    for label, present in _credential_status(settings):
        lines.append(f"{label:<12}{'set' if present else 'not set'}")

    for line in lines:
        typer.echo(line)


def _credential_status(settings: Settings) -> list[tuple[str, bool]]:
    return [
        ("soldcomps", settings.soldcomps_api_key is not None),
        ("anthropic", settings.anthropic_api_key is not None),
        ("apify", settings.apify_token is not None),
        ("ebay", settings.ebay_rest_config.is_file()),
    ]


if __name__ == "__main__":
    app()
