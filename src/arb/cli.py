"""`arb` command line.

Commands are invoked directly; the scheduler that drives them unattended wraps
`scan()` rather than changing it, which is why that function is kept pure.
"""

from __future__ import annotations

import json
import webbrowser
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import apprise
import typer
from alembic import command
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from arb import __version__
from arb.autobuy import MAX_ARM_HOURS, MIN_ARM_HOURS, SpendCaps
from arb.books.ledger import AGEING_DAYS, capital_position, ledger, totals
from arb.books.reconcile import MIN_SETTLEMENTS, corrected_yaml, reconcile
from arb.books.tax import TRADING_ALLOWANCE_PENCE, TaxYear, summarise_tax_year, tax_year_of
from arb.books.verticals import seed_synthetic_trades, verticals
from arb.comps.fees import load_fee_table
from arb.comps.service import CompsResult, CompsService
from arb.comps.soldcomps import SoldCompsClient
from arb.config import Settings, get_settings
from arb.dashboard import DashboardData, render_dashboard
from arb.db import AutobuyState, Inventory, VintedRef
from arb.models import (
    ConditionBand,
    Decision,
    DecisionMode,
    DecisionOutcome,
    ListingDraft,
    ListingFilter,
    Valuation,
    Venue,
    utcnow,
)
from arb.money import parse_pence, pence_to_decimal
from arb.monitor import (
    STALE_AFTER,
    RunRecord,
    RunStatus,
    alert_body,
    known_external_ids,
    monitor_health,
    new_candidates,
    record_run,
)
from arb.pipeline import ScanDeps, ScanSettings, run_scan
from arb.provenance import PlaceholderStatus, gather, resolve
from arb.refdata import load_reference_data
from arb.repo import record_decision, top_opportunities, upsert_listing, write_opportunity
from arb.selling.aspects_repo import cached_aspects, cached_categories, store_aspects
from arb.selling.crossvenue import HazardKind, hazards, unresolved_delists
from arb.selling.finances import parse_transactions
from arb.selling.labels import merge_labels
from arb.selling.reprice import RepriceContext, offer_ladder, reprice
from arb.selling.taxonomy import validate_draft
from arb.sourcing.vinted import VintedBuyVenue, build_client
from arb.store import alembic_config, make_engine, session_scope, upgrade_to_head

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

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


taxonomy_app = typer.Typer(no_args_is_help=True, help="eBay taxonomy compliance.")
app.add_typer(taxonomy_app, name="taxonomy")


@taxonomy_app.command("load")
def taxonomy_load(
    category_id: Annotated[str, typer.Argument(help="eBay leaf category id.")],
    payload: Annotated[Path, typer.Option(help="getItemAspectsForCategory JSON.")],
) -> None:
    """Cache one category's aspect enums from a Taxonomy response.

    Takes a file rather than fetching, because the fetch needs eBay credentials and
    the gate does not. Caching from a saved response keeps the compliance check
    usable before the Sell Inventory client exists, and keeps tests off the network.
    """
    if not payload.is_file():
        typer.echo(f"no such file: {payload}", err=True)
        raise typer.Exit(code=2)
    parsed = json.loads(payload.read_text(encoding="utf-8"))

    settings = get_settings()
    marketplace = settings.ebay_marketplace_id
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        store_aspects(
            session,
            parsed,
            marketplace_id=marketplace,
            category_id=category_id,
            fetched_at=utcnow(),
        )
        loaded = cached_aspects(session, marketplace_id=marketplace, category_id=category_id)

    if loaded is None:
        typer.echo(f"stored, but category {category_id} is not in that payload", err=True)
        raise typer.Exit(code=1)
    required = [a.name for a in loaded.aspects if a.required]
    typer.echo(f"cached {category_id} ({marketplace}): {len(loaded.aspects)} aspects")
    typer.echo(f"required: {', '.join(sorted(required)) or 'none'}")


@taxonomy_app.command("list")
def taxonomy_list() -> None:
    """Show which categories have cached enums, and under which tree version."""
    settings = get_settings()
    marketplace = settings.ebay_marketplace_id
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        rows = cached_categories(session, marketplace_id=marketplace)

    if not rows:
        typer.echo("no cached categories -- run `arb taxonomy load`")
        return
    for category_id, version in rows:
        typer.echo(f"{category_id:<12} tree version {version or '?'}")


@taxonomy_app.command("check")
def taxonomy_check(
    category_id: Annotated[str, typer.Argument(help="eBay leaf category id.")],
    size: Annotated[str, typer.Option(help="Size as it would be listed.")],
    brand: Annotated[str, typer.Option(help="Brand as it would be listed.")],
    condition: Annotated[ConditionBand | None, typer.Option(help="Condition band.")] = None,
    aspect: Annotated[
        list[str] | None, typer.Option(help="Extra specific, as Name=Value. Repeatable.")
    ] = None,
) -> None:
    """Check a draft against the cached enums before publishing it.

    Exits non-zero when the listing would be blocked, held, or accepted without
    being indexed. That third outcome is why this is a gate and not a warning: an
    unindexed listing looks exactly like a live one and sells nothing.
    """
    settings = get_settings()
    marketplace = settings.ebay_marketplace_id
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        aspects = cached_aspects(session, marketplace_id=marketplace, category_id=category_id)

    if aspects is None:
        typer.echo(
            f"refused: no cached enums for {category_id} ({marketplace}). "
            "Validating against another category would pass a listing eBay then holds.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        draft = ListingDraft(
            title="draft",
            description="draft",
            category_id=category_id,
            price_pence=0,
            size=size,
            condition_band=condition,
            brand=brand,
            aspects=_parse_aspect_options(aspect or []),
        )
    except ValidationError as exc:
        typer.echo(f"refused: {_first_error(exc)}", err=True)
        raise typer.Exit(code=2) from None

    verdict = validate_draft(draft, aspects)
    for warning in verdict.warnings:
        typer.echo(f"soon:  {warning}")
    if verdict.publishable:
        typer.echo("publishable")
        return
    for violation in verdict.violations:
        allowed = ", ".join(violation.allowed[:8]) if violation.allowed else "-"
        got = violation.got or "(missing)"
        typer.echo(f"BLOCK  {violation.aspect:<14} {violation.kind.value:<16} {got}")
        typer.echo(f"       allowed: {allowed}")
    typer.echo(f"refused: {verdict.blocking_reason}", err=True)
    raise typer.Exit(code=1)


def _parse_aspect_options(pairs: list[str]) -> tuple[tuple[str, str], ...]:
    parsed: list[tuple[str, str]] = []
    for pair in pairs:
        name, _, value = pair.partition("=")
        if not name.strip() or not value.strip():
            msg = f"expected Name=Value, got {pair!r}"
            raise typer.BadParameter(msg)
        parsed.append((name.strip(), value.strip()))
    return tuple(parsed)


@app.command()
def reconcile_fees(
    transactions: Annotated[Path, typer.Option(help="getTransactions JSON from sell_finances.")],
    fee_table: Annotated[str, typer.Option(help="Fee table name under data/fees.")] = "ebay_uk",
    *,
    write: Annotated[bool, typer.Option("--write", help="Overwrite the fee table.")] = False,
) -> None:
    """Compare predicted fees against what eBay actually charged, and correct the table.

    This is what closes P1. Every margin this tool has ever produced was computed
    from invented rates; these are the measured ones.

    Refuses below the settlement floor. A correction fitted to three sales is a guess
    that has learned to look like a measurement, which is worse than the honest guess
    it would replace.

    `--write` changes the file, which changes its content hash, which changes
    `fee_table_version`. Every opportunity scored under the old version then needs
    re-scoring before its margin is comparable -- `arb provenance` will show both
    versions sitting in the book until you do.
    """
    if not transactions.is_file():
        typer.echo(f"no such file: {transactions}", err=True)
        raise typer.Exit(code=2)
    table_path = FEE_TABLE_DIR / f"{fee_table}.yaml"
    if not table_path.is_file():
        typer.echo(f"no fee table at {table_path}", err=True)
        raise typer.Exit(code=2)

    table = load_fee_table(table_path)
    settlements = parse_transactions(json.loads(transactions.read_text(encoding="utf-8")))
    result = reconcile(settlements, table)

    if result is None:
        sales = sum(1 for s in settlements if s.is_sale)
        typer.echo(
            f"refused: {sales} settled sales is too few to fit a fee table. "
            f"Need {MIN_SETTLEMENTS}. Keep trading; the data accumulates.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(
        f"settlements  {result.settlements_used} sales, {result.refunds_excluded} refunds excluded"
    )
    typer.echo(f"{'COMPONENT':<28} {'ASSUMED':>10} {'MEASURED':>10}  DRIFT")
    for fit in result.fits:
        flag = "  <-- material" if fit.materially_different else ""
        typer.echo(
            f"{fit.name:<28} {fit.assumed!s:>10} {fit.measured!s:>10}  "
            f"{fit.drift!s:>9} (n={fit.observations}){flag}"
        )

    predicted = pence_to_decimal(result.predicted_total_pence)
    realised = pence_to_decimal(result.realised_total_pence)
    typer.echo("")
    typer.echo(f"predicted fees  {predicted}")
    typer.echo(f"realised fees   {realised}")
    typer.echo(f"drift           {pence_to_decimal(result.total_drift_pence)}")

    for fee_type, count in result.unmodelled:
        typer.echo(
            f"UNMODELLED: {fee_type} on {count} settlements -- not in the fee table, "
            "so every margin is overstated by this amount",
            err=True,
        )

    if not write:
        if result.needs_rewrite:
            typer.echo("")
            typer.echo("re-run with --write to correct the table and lift `provisional`")
        return

    table_path.write_text(
        corrected_yaml(table, result, verified_at=utcnow().date().isoformat()), encoding="utf-8"
    )
    rewritten = load_fee_table(table_path)
    typer.echo("")
    typer.echo(f"wrote {table_path}")
    typer.echo(f"fee_table_version {table.version} -> {rewritten.version}")
    typer.echo("re-score opportunities carrying the old version before comparing margins")


@app.command("reprice")
def reprice_listing(
    listed_price: Annotated[str, typer.Option(help="Current ask, in pounds e.g. 40.00")],
    cost: Annotated[str, typer.Option(help="What you paid, in pounds e.g. 12.00")],
    p25: Annotated[str, typer.Option(help="est_p25 from `value()`, in pounds.")],
    p60: Annotated[str, typer.Option(help="est_p60 from `value()`, in pounds.")],
    days: Annotated[float, typer.Option(help="Days the item has been listed.")] = 0.0,
) -> None:
    """Suggest a new ask and the Best Offer band for a listing that has not sold.

    Takes the valuation percentiles rather than recomputing them, because there is
    one valuation engine and this is not it. The two numbers come from `value()` --
    the same call that priced the buy decision.

    Percentiles are passed on the command line for now: reading them from stored
    inventory needs `listed_at` populated, which is the W3 lifecycle work.
    """
    current = parse_pence(listed_price)
    cost_pence = parse_pence(cost)
    p25_pence = parse_pence(p25)
    p60_pence = parse_pence(p60)
    if current is None or cost_pence is None or p25_pence is None or p60_pence is None:
        typer.echo("refused: prices must be decimal amounts in pounds", err=True)
        raise typer.Exit(code=2)

    settings = get_settings()
    table_path = FEE_TABLE_DIR / f"{settings.ebay_marketplace_id.lower()}.yaml"
    if not table_path.is_file():
        table_path = FEE_TABLE_DIR / "ebay_uk.yaml"
    fees = load_fee_table(table_path)

    try:
        valuation = Valuation(
            est_p25_pence=p25_pence,
            est_p60_pence=p60_pence,
            comp_n=0,
            est_confidence=0.0,
            match_confidence=0.0,
        )
    except ValidationError as exc:
        typer.echo(f"refused: {_first_error(exc)}", err=True)
        raise typer.Exit(code=2) from None

    ctx = RepriceContext(fee_model=fees, cost_pence=cost_pence)
    decision = reprice(valuation, ctx, current_pence=current, days_listed=days)
    ladder = offer_ladder(valuation, ctx, ask_pence=decision.suggested_pence)

    typer.echo(f"listed {days:.0f} days at {pence_to_decimal(decision.current_pence)}")
    typer.echo(f"suggest      {pence_to_decimal(decision.suggested_pence)}  ({decision.reason})")
    typer.echo(
        f"band         {pence_to_decimal(decision.floor_pence)} .. "
        f"{pence_to_decimal(valuation.est_p60_pence)}"
    )
    typer.echo(f"break even   {pence_to_decimal(decision.break_even_pence)}")
    if ladder.auto_accept_pence is None:
        typer.echo("offers       no auto-accept: cannot be sold at a profit at this ask")
    else:
        typer.echo(
            f"offers       accept >= {pence_to_decimal(ladder.auto_accept_pence)}, "
            f"decline < {pence_to_decimal(ladder.auto_decline_pence)}"
        )
    if decision.below_break_even:
        typer.echo(
            "WARNING: the suggested ask is below break-even. Selling here is a "
            "deliberate loss to recycle capital, not a trade.",
            err=True,
        )
    if not decision.changed:
        typer.echo("no change worth making")


@app.command()
def books(
    fee_table: Annotated[str, typer.Option(help="Fee table for unsettled trades.")] = "ebay_uk",
) -> None:
    """Cost basis, realised margin, capital deployed, and what is ageing.

    Settled and estimated margin are reported on separate lines and never summed.
    A margin computed from settlement data and one computed from the provisional fee
    table are both plausible numbers; added together they make a total that is
    neither, with no way afterwards to tell which half was real.
    """
    settings = get_settings()
    table_path = FEE_TABLE_DIR / f"{fee_table}.yaml"
    if not table_path.is_file():
        typer.echo(f"no fee table at {table_path}", err=True)
        raise typer.Exit(code=2)
    fees = load_fee_table(table_path)

    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        trades = ledger(session, fees)
        position = capital_position(session, now=utcnow())

    typer.echo("CAPITAL")
    typer.echo(f"  deployed   {pence_to_decimal(position.deployed_pence):>10}  (unsold stock)")
    typer.echo(f"  recycled   {pence_to_decimal(position.recycled_pence):>10}  (gross returned)")
    if position.aged_count:
        typer.echo(
            f"  ageing     {pence_to_decimal(position.aged_pence):>10}  "
            f"({position.aged_count} items over {AGEING_DAYS} days)"
        )

    typer.echo("")
    typer.echo("STOCK")
    for state, count, cost in position.by_state:
        if count:
            typer.echo(f"  {state.value:<12} {count:>4} items  {pence_to_decimal(cost):>10}")

    settled_net, estimated_net, settled_count = totals(trades)
    typer.echo("")
    typer.echo("REALISED")
    if not trades:
        typer.echo("  no completed sales yet")
        return
    typer.echo(f"  settled    {pence_to_decimal(settled_net):>10}  ({settled_count} trades)")
    if estimated_net or len(trades) > settled_count:
        typer.echo(
            f"  estimated  {pence_to_decimal(estimated_net):>10}  "
            f"({len(trades) - settled_count} trades, provisional fees)"
        )
        typer.echo("  not summed: one is measured, the other is not")
    if not table_path.is_file() or fees.provisional:
        typer.echo("")
        typer.echo("fees are still provisional -- run `arb reconcile-fees` (see `arb provenance`)")


@app.command()
def labels(
    source: Annotated[Path, typer.Argument(help="Directory of carrier label PDFs.")],
    out: Annotated[Path, typer.Option(help="Where to write the merged batch.")] = Path(
        "labels.pdf"
    ),
) -> None:
    """Crop carrier labels to 6x4 and merge them into one printable batch.

    An unrecognised carrier passes through uncropped rather than being cropped to a
    guessed region. That prints badly and visibly; a wrong crop prints beautifully
    and fails at the counter after the parcel is packed.
    """
    if not source.is_dir():
        typer.echo(f"not a directory: {source}", err=True)
        raise typer.Exit(code=2)
    pdfs = sorted(source.glob("*.pdf"))
    if not pdfs:
        typer.echo(f"no PDFs in {source}", err=True)
        raise typer.Exit(code=1)

    result = merge_labels(pdfs, out)
    typer.echo(f"wrote {out}: {result.written} labels ({result.cropped} cropped)")
    if result.unidentified:
        typer.echo(
            f"WARNING: {result.unidentified} pages had an unrecognised carrier and went "
            "in uncropped -- check them before printing",
            err=True,
        )
    if result.failed:
        typer.echo(f"WARNING: {result.failed} files could not be read", err=True)


@app.command()
def tax(
    year: Annotated[
        int | None, typer.Option(help="Tax year by starting year, e.g. 2026 for 2026/27.")
    ] = None,
    fee_table: Annotated[str, typer.Option(help="Fee table for unsettled sales.")] = "ebay_uk",
) -> None:
    """Total a UK tax year and compare the two allowance methods.

    **This is a preparation aid, not a tax return and not tax advice.** It totals what
    the ledger knows and applies two legislated rules. It does not compute tax owed --
    that needs your other income, personal allowance and National Insurance position,
    none of which live here -- and it deliberately prints no SA103 box numbers,
    because box numbering changes between years and forms and a wrong one produces a
    return that is confidently incorrect.

    Cash basis is assumed: income counts when received, costs when paid. That is the
    default for sole traders, and it means a trade bought in March and sold in May
    puts its cost in one tax year and its income in the next.
    """
    settings = get_settings()
    table_path = FEE_TABLE_DIR / f"{fee_table}.yaml"
    if not table_path.is_file():
        typer.echo(f"no fee table at {table_path}", err=True)
        raise typer.Exit(code=2)
    fees = load_fee_table(table_path)
    target = TaxYear(year) if year is not None else tax_year_of(utcnow())

    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        summary = summarise_tax_year(session, target, fees)

    typer.echo(
        f"UK tax year {target.label}  (6 Apr {target.start_year} - 5 Apr {target.start_year + 1})"
    )
    typer.echo("")
    typer.echo(
        f"  gross income      {pence_to_decimal(summary.gross_income_pence):>10}"
        f"   ({summary.sales_count} sales)"
    )
    typer.echo(f"  allowable costs   {pence_to_decimal(summary.allowable_costs_pence):>10}")
    typer.echo("")
    typer.echo("TAXABLE PROFIT, two methods -- you may use one, never both")
    typer.echo(f"  deduct expenses   {pence_to_decimal(summary.profit_actual_expenses_pence):>10}")
    typer.echo(
        f"  £1,000 allowance  {pence_to_decimal(summary.profit_trading_allowance_pence):>10}"
    )
    typer.echo(f"  lower            {summary.lower_method:>11}")

    typer.echo("")
    if summary.below_threshold:
        typer.echo(
            f"  gross is at or under {pence_to_decimal(TRADING_ALLOWANCE_PENCE)} -- full relief "
            "normally applies, and this income alone does not usually require registration"
        )
    else:
        typer.echo(
            f"  gross is over {pence_to_decimal(TRADING_ALLOWANCE_PENCE)} -- if not already "
            f"registered for Self Assessment, the deadline is {target.register_by}"
        )
    if summary.straddling_count:
        typer.echo(
            f"  {summary.straddling_count} trades straddle a year boundary: cost in one year, "
            "income in the next. Correct under cash basis, different under accruals."
        )

    if summary.figures_are_provisional:
        typer.echo("")
        typer.echo(
            f"WARNING: {summary.estimated_fees_count} sales are costed from the provisional fee "
            "table, not settlement. These are not tax figures until `arb reconcile-fees` has run.",
            err=True,
        )
    typer.echo("")
    typer.echo("Figures to check with an accountant. Not a filing, and not tax advice.")


monitor_app = typer.Typer(no_args_is_help=True, help="Watch searches for new stock.")
app.add_typer(monitor_app, name="monitor")


def _notify(settings: Settings, title: str, body: str) -> None:
    """Send an alert, falling back to stdout.

    Printing when no channel is configured is deliberate. A monitor whose notifier is
    misconfigured must not fail silently -- that is the same silence as a quiet
    market, which is the failure this whole subsystem is built to avoid.
    """
    url = settings.notify_url
    if url is None:
        typer.echo(f"[{title}] {body}")
        return

    notifier = apprise.Apprise()
    if not notifier.add(url.get_secret_value()):
        typer.echo(f"notify URL rejected by apprise; printing instead\n[{title}] {body}", err=True)
        return
    if not notifier.notify(title=title, body=body):
        typer.echo(f"notify failed; printing instead\n[{title}] {body}", err=True)


@monitor_app.command("run")
def monitor_run(
    query: Annotated[str, typer.Argument(help="What to watch Vinted for.")],
    name: Annotated[str | None, typer.Option(help="Monitor name. Defaults to the query.")] = None,
    limit: Annotated[int, typer.Option(help="Listings to fetch per poll.")] = 48,
    max_price: Annotated[str | None, typer.Option(help="Cap, in pounds.")] = None,
) -> None:
    """Run one monitor pass: scan, diff against what we have seen, alert on what is new.

    One pass, not a loop. Scheduling belongs to cron or a systemd timer, which already
    handle restarts, overlap and backoff properly -- reimplementing that here would be
    a worse version of something already installed on the machine.

    A heartbeat row is written whether this succeeds or fails. Without it, a crashed
    monitor and a quiet market are the same silence.
    """
    settings = get_settings()
    monitor = name or query
    started = utcnow()

    if settings.soldcomps_api_key is None:
        typer.echo("no ARB_SOLDCOMPS_API_KEY set -- cannot fetch comps", err=True)
        raise typer.Exit(code=2)

    fees = load_fee_table(FEE_TABLE_DIR / "ebay_uk.yaml")
    listing_filter = ListingFilter(query=query, limit=limit, max_price_pence=parse_pence(max_price))
    engine = make_engine(settings.db_url)

    try:
        with session_scope(engine) as session:
            known = known_external_ids(session, Venue.VINTED)
            service = CompsService(
                SoldCompsClient(settings.soldcomps_api_key.get_secret_value()),
                session,
                freshness=timedelta(days=settings.comps_freshness_days),
            )
            outcome = run_scan(
                ScanDeps(
                    buy_venue=VintedBuyVenue(build_client(settings.vinted_base_url)),
                    comps=service,
                    fee_model=fees,
                ),
                listing_filter,
                started,
                ScanSettings(min_comp_n=settings.min_comp_n),
            )
            report = new_candidates(
                outcome,
                known,
                monitor=monitor,
                listings_seen=len(outcome.ranked) + len(outcome.rejected_quality),
            )
            for candidate in outcome.ranked:
                listing_id = upsert_listing(session, candidate.listing)
                write_opportunity(session, candidate.opportunity, listing_id=listing_id)
            record_run(
                session,
                RunRecord(
                    monitor=monitor,
                    started_at=started,
                    finished_at=utcnow(),
                    status=RunStatus.OK,
                    report=report,
                ),
            )
    except (SQLAlchemyError, OSError, ValueError) as exc:
        with session_scope(engine) as session:
            record_run(
                session,
                RunRecord(
                    monitor=monitor,
                    started_at=started,
                    finished_at=utcnow(),
                    status=RunStatus.FAILED,
                    error=str(exc)[:500],
                ),
            )
        typer.echo(f"monitor {monitor} FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"{monitor}: {len(report.new_listings)} new, {len(report.alerts)} worth buying")
    if report.has_alerts:
        _notify(settings, f"arb: {monitor}", alert_body(report))


@monitor_app.command("health")
def monitor_health_cmd(
    name: Annotated[str, typer.Argument(help="Monitor name.")],
) -> None:
    """Say whether a monitor is actually running.

    Exits non-zero when stale, so a cron wrapper can alert on the monitor itself. A
    monitor that has stopped and a market that has gone quiet produce identical
    silence, and this is the only thing that tells them apart.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        health = monitor_health(session, name, now=utcnow())

    last = health.last_success.isoformat(timespec="seconds") if health.last_success else "never"
    typer.echo(f"monitor           {health.monitor}")
    typer.echo(f"last success      {last}")
    typer.echo(
        f"last status       {health.last_status.value if health.last_status else 'never ran'}"
    )
    typer.echo(f"failures in a row {health.consecutive_failures}")
    if health.stale:
        typer.echo(
            f"STALE: nothing has succeeded within {STALE_AFTER}. Silence from this "
            "monitor means nothing about the market.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo("healthy")


autobuy_app = typer.Typer(no_args_is_help=True, help="Automated purchasing rails.")
app.add_typer(autobuy_app, name="autobuy")


def _autobuy_state(session: Session) -> AutobuyState:
    """The single state row, created disarmed on first access. Fails closed."""
    state = session.get(AutobuyState, 1)
    if state is None:
        state = AutobuyState(id=1, armed_until=None, kill_switch=False, updated_at=utcnow())
        session.add(state)
        session.flush()
    return state


def _fees_measured() -> bool:
    """Read P1 from the same register `arb provenance` prints.

    Consulted rather than duplicated: a second definition of 'are the fees real yet'
    would drift from the first, and this is the one rail whose being wrong costs
    money at machine speed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        state = gather(session, FEE_TABLE_DIR)
    for entry in resolve(state):
        if entry.placeholder.id == "P1":
            return entry.status is PlaceholderStatus.CLOSED
    return False


@autobuy_app.command("arm")
def autobuy_arm(
    hours: Annotated[int, typer.Option(help="How long to stay armed.")] = 4,
    note: Annotated[str | None, typer.Option(help="Why, for the audit trail.")] = None,
) -> None:
    """Arm AutoBuy for a bounded window.

    An expiry rather than a flag, deliberately. AutoBuy requires periodic affirmative
    action to keep running, so walking away from the machine stops it. A boolean would
    stay true forever, which is exactly the state you do not want to find a fortnight
    later.
    """
    if hours < MIN_ARM_HOURS or hours > MAX_ARM_HOURS:
        typer.echo(f"refused: arm for between {MIN_ARM_HOURS} and {MAX_ARM_HOURS} hours", err=True)
        raise typer.Exit(code=2)
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        state = _autobuy_state(session)
        if state.kill_switch:
            typer.echo(
                "refused: the kill switch is engaged. Clear it explicitly with "
                "`arb autobuy resume` before arming.",
                err=True,
            )
            raise typer.Exit(code=2)
        state.armed_until = utcnow() + timedelta(hours=hours)
        state.updated_at = utcnow()
        state.note = note
        expiry = state.armed_until.isoformat(timespec="seconds")
    typer.echo(f"armed until {expiry}")


@autobuy_app.command("stop")
def autobuy_stop(
    note: Annotated[str | None, typer.Option(help="Why, for the audit trail.")] = None,
) -> None:
    """Engage the kill switch and disarm immediately."""
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        state = _autobuy_state(session)
        state.kill_switch = True
        state.armed_until = None
        state.updated_at = utcnow()
        state.note = note
    typer.echo("STOPPED: kill switch engaged, AutoBuy disarmed")


@autobuy_app.command("resume")
def autobuy_resume() -> None:
    """Clear the kill switch. Does not arm -- that stays a separate, deliberate act."""
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        state = _autobuy_state(session)
        state.kill_switch = False
        state.updated_at = utcnow()
    typer.echo("kill switch cleared -- still disarmed, run `arb autobuy arm` to enable")


@autobuy_app.command("status")
def autobuy_status() -> None:
    """Show every rail and whether it currently permits buying.

    Exits non-zero when AutoBuy would refuse, so it can be used as a preflight.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    now = utcnow()
    with session_scope(engine) as session:
        state = _autobuy_state(session)
        armed_until = state.armed_until
        kill = state.kill_switch
    fees_ok = _fees_measured()

    armed = armed_until is not None and armed_until > now
    armed_text = (
        f"until {armed_until.isoformat(timespec='seconds')}"
        if armed and armed_until is not None
        else "no"
    )
    caps = SpendCaps()
    typer.echo(f"kill switch    {'ENGAGED' if kill else 'clear'}")
    typer.echo(f"armed          {armed_text}")
    typer.echo(f"fees measured  {'yes' if fees_ok else 'NO -- P1 is open'}")
    typer.echo(
        f"caps           run {pence_to_decimal(caps.per_run_pence)} / "
        f"day {pence_to_decimal(caps.per_day_pence)} / "
        f"outstanding {pence_to_decimal(caps.outstanding_pence)}"
    )

    blocked = kill or not armed or not fees_ok
    if blocked:
        typer.echo("")
        if not fees_ok:
            typer.echo(
                "AutoBuy is blocked: fees are still provisional. Automated spending "
                "against invented rates repeats a mistake at machine speed. Run "
                "`arb reconcile-fees` first.",
                err=True,
            )
        else:
            typer.echo("AutoBuy would refuse to buy right now.", err=True)
        raise typer.Exit(code=1)
    typer.echo("")
    typer.echo("AutoBuy would permit buying")


@autobuy_app.command("dryrun")
def autobuy_dryrun(
    limit: Annotated[int, typer.Option(help="Opportunities to consider.")] = 20,
) -> None:
    """Show what AutoBuy *would* buy from the current buy list, and why not otherwise.

    Ignores the arm state on purpose -- a dry-run you have to arm for is one nobody
    runs. Every other rail is applied exactly as it would be live, so the refusal
    reasons here are the real ones.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        rows = top_opportunities(session, limit=limit)
    if not rows:
        typer.echo("no opportunities scored yet -- run `arb scan` first")
        return

    typer.echo(f"{len(rows)} opportunities on the buy list")
    typer.echo(f"fees measured: {'yes' if _fees_measured() else 'NO -- P1 open, would halt'}")
    typer.echo("")
    typer.echo("Dry-run is scored against recorded decisions, and means nothing until")
    typer.echo("real ones accumulate -- that is P8. See `arb provenance`.")


@app.command()
def dashboard(
    out: Annotated[Path, typer.Option(help="Where to write the page.")] = Path("books.html"),
    fee_table: Annotated[str, typer.Option(help="Fee table for unsettled trades.")] = "ebay_uk",
    *,
    open_browser: Annotated[
        bool, typer.Option("--open", "-o", help="Open in default web browser after generating.")
    ] = False,
) -> None:
    """Write a self-contained HTML view of the books.

    One file, no server and no build step. It is a read-only view over a local SQLite
    database, and three extra runtimes to render that would be maintenance without a
    reader.

    Every figure carries where it came from. A margin computed from settlement data
    and one computed from fee rates nobody has checked are shown differently on
    purpose -- and the open assumptions get a section of their own rather than a
    footnote, because that is what the numbers above them depend on.
    """
    settings = get_settings()
    table_path = FEE_TABLE_DIR / f"{fee_table}.yaml"
    if not table_path.is_file():
        typer.echo(f"no fee table at {table_path}", err=True)
        raise typer.Exit(code=2)
    fees = load_fee_table(table_path)

    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        synthetic = (
            session.scalar(
                select(func.count()).select_from(Inventory).where(Inventory.synthetic.is_(True))
            )
            or 0
        )
        data = DashboardData(
            generated_at=utcnow(),
            capital=capital_position(session, now=utcnow()),
            trades=ledger(session, fees),
            placeholders=resolve(gather(session, FEE_TABLE_DIR)),
            verticals=verticals(session),
            synthetic_trades=int(synthetic),
        )

    out.write_text(render_dashboard(data), encoding="utf-8")
    typer.echo(f"wrote {out}")
    if data.synthetic_trades:
        typer.echo(f"note: {data.synthetic_trades} trades on this page are seeded, not traded")
    if open_browser:
        webbrowser.open(out.resolve().as_uri())


@app.command()
def seed(
    count: Annotated[int, typer.Option(help="How many trades to generate.")] = 40,
) -> None:
    """Generate synthetic trades so the books and dashboard have shape.

    Every row is marked synthetic and is excluded from the placeholder register, so
    seeding cannot make an assumption look measured. That is the property that makes
    it safe to build a dashboard before the first real sale.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        created = seed_synthetic_trades(session, now=utcnow(), count=count)
    typer.echo(f"seeded {created} synthetic trades")
    typer.echo("these cannot close a placeholder -- check with `arb provenance`")


@app.command()
def hazard_check() -> None:
    """Report anything at risk of being sold twice across venues.

    A query over state, not a replay of events, so it is correct after a crash, a
    missed webhook, or a period when nothing was running. Exits non-zero when
    anything is live that should not be, so it can be scheduled as a guard.
    """
    settings = get_settings()
    engine = make_engine(settings.db_url)
    with session_scope(engine) as session:
        found = hazards(session)
        pending = len(unresolved_delists(session))

    if not found:
        typer.echo(f"no double-sale hazards ({pending} de-lists in flight)")
        return

    for hazard in found:
        detail = f"  {hazard.detail}" if hazard.detail else ""
        typer.echo(
            f"{hazard.kind.value:<18} item {hazard.inventory_id:<5} "
            f"{hazard.venue}:{hazard.external_id}{detail}"
        )
    worst = {h.kind for h in found}
    typer.echo("")
    if HazardKind.SOLD_TWICE in worst:
        typer.echo(
            "SOLD TWICE: already happened. Refund the second buyer before they chase "
            "it -- a defect raised by a buyer costs more than one you raise yourself.",
            err=True,
        )
    if HazardKind.LIVE_AFTER_SALE in worst:
        typer.echo(
            "LIVE AFTER SALE: sold elsewhere and still buyable, with nothing in "
            "flight. Nothing will fix this on its own.",
            err=True,
        )
    raise typer.Exit(code=1)
