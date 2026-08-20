"""`arb` command line.

Commands are invoked directly; the scheduler that drives them unattended wraps
`scan()` rather than changing it, which is why that function is kept pure.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from alembic import command
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from arb import __version__
from arb.comps.fees import load_fee_table
from arb.comps.service import CompsResult, CompsService
from arb.comps.soldcomps import SoldCompsClient
from arb.config import Settings, get_settings
from arb.db import VintedRef
from arb.models import Decision, DecisionMode, DecisionOutcome, ListingFilter, utcnow
from arb.money import parse_pence
from arb.pipeline import ScanDeps, ScanSettings, run_scan
from arb.provenance import PlaceholderStatus, gather, resolve
from arb.refdata import load_reference_data
from arb.repo import record_decision, top_opportunities, upsert_listing, write_opportunity
from arb.sourcing.vinted import VintedBuyVenue, build_client
from arb.store import alembic_config, make_engine, session_scope, upgrade_to_head

if TYPE_CHECKING:
    from arb.sourcing.scanner import ScanOutcome

FEE_TABLE_DIR = Path(__file__).resolve().parent / "data" / "fees"

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


@app.command("buylist")
def buylist(
    limit: Annotated[int, typer.Option(help="How many opportunities to show.")] = 20,
) -> None:
    """Show the current buy list, best capital velocity first.

    Reads what `scan` last wrote. Each row's ID is what `arb decide` takes, so the
    loop is: buylist, look, decide -- and every decision lands in the same table
    AutoBuy would eventually write to.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        rows = top_opportunities(session, limit=limit)

    if not rows:
        typer.echo("no opportunities scored yet -- run `arb scan` first")
        return

    typer.echo(f"{'ID':>5}  {'NET':>8}  {'ROI':>6}  {'VEL':>7}  {'CONF':>5}  ITEM")
    for opportunity, listing in rows:
        velocity = opportunity.capital_velocity
        typer.echo(
            f"{opportunity.id:>5}  "
            f"{opportunity.net_pence / 100:>7.2f}  "
            f"{opportunity.roi:>5.0%}  "
            f"{(f'{velocity:.4f}' if velocity is not None else '--'):>7}  "
            f"{opportunity.est_confidence:>5.2f}  "
            f"{(listing.title_norm or '')[:48]}"
        )


@app.command()
def decide(
    opportunity_id: Annotated[int, typer.Argument(help="ID from `arb buylist`.")],
    outcome: Annotated[DecisionOutcome, typer.Option(help="bought or skipped.")],
    reason: Annotated[str | None, typer.Option(help="Why you skipped. Required on a skip.")] = None,
    spend: Annotated[
        str | None, typer.Option(help="What you actually paid, in pounds e.g. 12.50")
    ] = None,
) -> None:
    """Record a buy or a skip against an opportunity.

    A skip needs a reason and the command refuses without one. That is not form
    validation: AutoBuy's dry-run scores itself by replaying candidates and diffing
    against these rows, and without reasons it has nothing to score against.
    """
    settings = get_settings()
    if outcome is DecisionOutcome.BOUGHT and spend is None:
        # Without a cost basis the ledger cannot compute realised margin, and a
        # purchase recorded at zero would quietly overstate every downstream figure.
        typer.echo("refused: --spend is required when recording a purchase", err=True)
        raise typer.Exit(code=2)
    try:
        record = Decision(
            opportunity_id=opportunity_id,
            mode=DecisionMode.MANUAL,
            outcome=outcome,
            skip_reason=reason,
            decided_at=utcnow(),
            spend_pence=parse_pence(spend),
        )
    except ValidationError as exc:
        typer.echo(f"refused: {_first_error(exc)}", err=True)
        raise typer.Exit(code=2) from None

    engine = make_engine(settings.db_url)
    try:
        with session_scope(engine) as session:
            decision_id = record_decision(session, record)
    except IntegrityError:
        typer.echo(f"no opportunity with id {opportunity_id}", err=True)
        raise typer.Exit(code=2) from None

    typer.echo(f"recorded decision {decision_id}: {outcome.value}")


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    return str(errors[0].get("msg", exc)) if errors else str(exc)


@app.command()
def scan(
    query: Annotated[str, typer.Argument(help="What to search Vinted for.")],
    limit: Annotated[int, typer.Option(help="Listings to fetch.")] = 48,
    max_price: Annotated[str | None, typer.Option(help="Cap, in pounds e.g. 30.00")] = None,
    fee_table: Annotated[str, typer.Option(help="Fee table name under data/fees.")] = "ebay_uk",
) -> None:
    """Fetch Vinted listings, price them against eBay comps, and write a buy list.

    Comps are served from the append-only cache when they are fresh enough. That is
    not an optimisation: the free tier is 100 requests a month, and a scan that
    fetched per listing would spend it in one run.
    """
    settings = get_settings()
    if settings.soldcomps_api_key is None:
        typer.echo("no ARB_SOLDCOMPS_API_KEY set -- cannot fetch comps", err=True)
        raise typer.Exit(code=2)

    table_path = FEE_TABLE_DIR / f"{fee_table}.yaml"
    if not table_path.is_file():
        typer.echo(f"no fee table at {table_path}", err=True)
        raise typer.Exit(code=2)
    fees = load_fee_table(table_path)

    buy_venue = VintedBuyVenue(build_client(settings.vinted_base_url))

    listing_filter = ListingFilter(query=query, limit=limit, max_price_pence=parse_pence(max_price))
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        service = CompsService(
            SoldCompsClient(settings.soldcomps_api_key.get_secret_value()),
            session,
            freshness=timedelta(days=settings.comps_freshness_days),
        )
        outcome = run_scan(
            ScanDeps(buy_venue=buy_venue, comps=service, fee_model=fees),
            listing_filter,
            utcnow(),
            ScanSettings(min_comp_n=settings.min_comp_n),
        )
        for candidate in outcome.ranked:
            listing_id = upsert_listing(session, candidate.listing)
            write_opportunity(session, candidate.opportunity, listing_id=listing_id)

    _report_scan(outcome, service.stats)


def _report_scan(outcome: ScanOutcome, stats: CompsResult) -> None:
    """Print what the scan did *and* what it dropped.

    The suppression counts are the point. An empty buy list because the market is
    quiet, because nothing could be priced, and because the comps quota ran out are
    three different situations that a bare list cannot tell apart.
    """
    result = outcome.result
    typer.echo(f"ranked                {len(outcome.ranked)}")
    typer.echo(f"rejected on quality   {len(outcome.rejected_quality)}")
    typer.echo(f"too contested         {len(outcome.rejected_contest)}")
    typer.echo(f"unpriceable           {len(outcome.unpriceable)}")
    typer.echo(f"below profit floor    {result.suppressed_below_floor}")
    typer.echo(f"unknown sell speed    {result.suppressed_unknown_velocity}")
    if result.suppressed_anomalous_cost:
        typer.echo(f"anomalous cost        {result.suppressed_anomalous_cost}")
    typer.echo(f"comps: {stats.cache_hits} cached, {stats.fetches} fetched")
    if stats.quota_exhausted:
        typer.echo(
            "WARNING: comps quota exhausted mid-scan -- this buy list is incomplete",
            err=True,
        )
    if not outcome.ranked:
        typer.echo("nothing to buy from this search")


@app.command()
def provenance() -> None:
    """Show which numbers here are still assumptions rather than measurements.

    Almost everything this tool prints rests on something nobody has checked -- the
    fees are invented, postage is a constant, the quality lexicon is a first guess.
    That is a defensible way to build, but only while the assumptions stay visible.
    This is the command that keeps them visible.

    It reports rather than fails. Every placeholder is open early on, so exiting
    non-zero would make the command useless exactly when it is most needed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    try:
        with session_scope(engine) as session:
            state = gather(session, FEE_TABLE_DIR)
    except SQLAlchemyError as exc:
        typer.echo(f"db UNUSABLE: {exc}", err=True)
        raise typer.Exit(code=1) from None

    resolved = resolve(state)
    typer.echo(f"{'ID':<4} {'STATUS':<8} {'GAP':<30} EVIDENCE")
    for entry in resolved:
        typer.echo(
            f"{entry.placeholder.id:<4} "
            f"{entry.status.value:<8} "
            f"{entry.placeholder.gap[:30]:<30} "
            f"{entry.evidence}"
        )

    counts = Counter(entry.status for entry in resolved)
    typer.echo("")
    typer.echo(
        f"{len(resolved)} placeholders: "
        f"{counts[PlaceholderStatus.OPEN]} open, "
        f"{counts[PlaceholderStatus.CLOSED]} closed, "
        f"{counts[PlaceholderStatus.UNKNOWN]} unknown"
    )
    _report_fee_versions(state.fee_versions_in_use)


def _report_fee_versions(versions: tuple[tuple[str, int], ...]) -> None:
    """Show which fee assumptions scored the existing book.

    More than one version means opportunities in the same table were costed under
    different assumptions, so their margins cannot be compared with each other. That
    is the situation `fee_table_version` was stamped for: it makes the affected rows
    findable, and a re-score possible.
    """
    if not versions:
        return
    typer.echo("")
    typer.echo("fee versions scoring the book")
    for version, count in versions:
        typer.echo(f"  {version:<28} {count:>6}")
    if len(versions) > 1:
        typer.echo(
            f"WARNING: {len(versions)} fee versions in one book -- margins are not "
            "comparable until you re-score",
            err=True,
        )
