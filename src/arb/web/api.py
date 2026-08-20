"""REST surface over the existing services. No business logic lives here.

Every endpoint is a thin adapter onto a function that already exists and is already
tested. That constraint is the point: a web layer that grows its own valuation, its own
fee arithmetic or its own idea of what "settled" means becomes a second implementation
that drifts from the first, and the two then disagree while both look authoritative.
If a route here needs a calculation, the calculation belongs in `books/`, `comps/` or
`selling/` and the route calls it.

Four places where the interface deliberately says less than a dashboard usually would,
each because the underlying system refuses to claim it:

**No tax liability figure.** `books/tax.py` computes turnover, allowable costs and the
two candidate profit figures, and stops. Tax owed needs other income, personal
allowance, Scottish rates and a National Insurance position, none of which this system
holds. A "live tax estimate" would be a confident number about a legal obligation,
assembled from a quarter of the inputs.

**The ledger is single-entry and is labelled as such.** It records cost basis against
realised proceeds. Calling it double-entry would be a false claim about the books, and
the person most misled would be the one who relies on it.

**Reconciliation is not one click.** `--write` rewrites a content-hashed fee table and
bumps `fee_table_version`, which invalidates the comparability of every score computed
under the old one. The route previews by default and requires an explicit confirm.

**De-listing is requested, not performed.** `selling/crossvenue` separates intent from
confirmation because the venue call fails independently. A control that reported
success on click would be inventing the confirmation.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from arb.books.ledger import (
    FUNDS_CLEARED,
    LifecycleState,
    capital_position,
    ledger,
    totals,
)
from arb.books.tax import TRADING_ALLOWANCE_PENCE, TaxYear, summarise_tax_year, tax_year_of
from arb.books.verticals import verticals
from arb.comps.fees import FeeTable, load_fee_table
from arb.config import get_settings
from arb.db import Inventory, OwnListings
from arb.models import Decision, DecisionMode, DecisionOutcome, utcnow
from arb.money import parse_pence, pence_to_decimal
from arb.monitor import monitor_health
from arb.provenance import PlaceholderStatus, gather, resolve
from arb.repo import record_decision, top_opportunities
from arb.selling.crossvenue import hazards, request_delists, unresolved_delists
from arb.store import make_engine, session_scope

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

__all__ = ["FEE_TABLE_DIR", "SKIP_REASONS", "router"]

FEE_TABLE_DIR = Path(__file__).resolve().parent.parent / "data" / "fees"

SKIP_REASONS: tuple[str, ...] = (
    "contest_density_too_high",
    "margin_below_floor",
    "velocity_unknown",
    "quality_concern",
    "size_hard_to_sell",
    "capital_committed_elsewhere",
    "other",
)
"""The vocabulary a skip may use. Fixed rather than free text because AutoBuy's
dry-run scores itself by diffing against these rows, and a hundred spellings of "too
expensive" cannot be compared with anything."""

router = APIRouter(prefix="/api/v1")


def get_session() -> Iterator[Session]:
    """One session per request, committed on success and rolled back on error."""
    engine = make_engine(get_settings().db_url)
    with session_scope(engine) as session:
        yield session


SessionDep = Annotated["Session", Depends(get_session)]


def _fees() -> FeeTable:
    """The active fee table. Loaded per request so an `arb reconcile-fees --write`
    is visible without restarting the server -- and, more importantly, so the version
    shown on screen is the version currently in force."""
    return load_fee_table(FEE_TABLE_DIR / "ebay_uk.yaml")


def _money(pence: int) -> str:
    return str(pence_to_decimal(pence))


# ------------------------------------------------------------------ schemas


class MetricCard(BaseModel):
    label: str
    value: str
    measured: bool | None = None
    """`None` when the measured/assumed distinction does not apply. The frontend
    colours on this and nothing else."""

    note: str | None = None


class PipelineStage(BaseModel):
    key: str
    label: str
    count: int
    cost_pence: int
    derived: bool = False


class DashboardResponse(BaseModel):
    metrics: list[MetricCard]
    pipeline: list[PipelineStage]
    verticals: list[dict[str, object]]
    synthetic_trades: int
    open_placeholders: int


class DecisionRequest(BaseModel):
    opportunity_id: int
    outcome: Literal["bought", "skipped"]
    skip_reason: str | None = None
    spend: str | None = Field(default=None, description="Pounds, e.g. 12.50")


# ------------------------------------------------------------------ dashboard


@router.get("/dashboard", response_model=DashboardResponse)
def read_dashboard(session: SessionDep) -> DashboardResponse:
    """Headline figures, the pipeline, and how much of it rests on assumptions."""
    fees = _fees()
    now = utcnow()
    position = capital_position(session, now=now)
    trades = ledger(session, fees)
    settled_net, estimated_net, settled_count = totals(trades)
    placeholders = resolve(gather(session, FEE_TABLE_DIR))

    cleared = (
        session.scalar(
            select(func.count())
            .select_from(Inventory)
            .where(Inventory.actual_fees_pence.is_not(None))
        )
        or 0
    )
    synthetic = (
        session.scalar(
            select(func.count()).select_from(Inventory).where(Inventory.synthetic.is_(True))
        )
        or 0
    )

    gross = sum(t.gross_pence for t in trades)
    margin = (settled_net + estimated_net) / gross if gross else 0.0
    # Annualised from the span the trades actually cover, floored at 30 days. A run
    # rate extrapolated from a fortnight is arithmetic rather than information, and
    # the floor stops one good week reading as a six-figure year.
    span_days = max(sum(t.days_held or 0 for t in trades), 30)
    run_rate = int((settled_net + estimated_net) * 365 / span_days) if trades else 0

    metrics = [
        MetricCard(
            label="Deployed capital",
            value=f"£{_money(position.deployed_pence)}",
            note="cost basis of unsold stock",
        ),
        MetricCard(
            label="Recycled capital",
            value=f"£{_money(position.recycled_pence)}",
            note="gross returned by sales",
        ),
        MetricCard(
            label="Realised net — settled",
            value=f"£{_money(settled_net)}",
            measured=True,
            note=f"{settled_count} trades from settlement data",
        ),
        MetricCard(
            label="Realised net — estimated",
            value=f"£{_money(estimated_net)}",
            measured=False,
            note=f"{len(trades) - settled_count} trades on provisional fees",
        ),
        MetricCard(
            label="Annualised run rate",
            value=f"£{_money(run_rate)}",
            measured=False,
            note="extrapolated from a short and partly estimated history",
        ),
        MetricCard(
            label="Net margin",
            value=f"{margin:.1%}",
            measured=False,
            note="blends settled and estimated trades",
        ),
    ]

    by_state = {state: (count, cost) for state, count, cost in position.by_state}
    pipeline = [
        PipelineStage(
            key=state.value,
            label=state.value.replace("_", " ").title(),
            count=by_state.get(state, (0, 0))[0],
            cost_pence=by_state.get(state, (0, 0))[1],
        )
        for state in LifecycleState
    ]
    pipeline.append(
        PipelineStage(
            key=FUNDS_CLEARED,
            label="Funds Cleared",
            count=int(cleared),
            cost_pence=0,
            derived=True,
        )
    )

    return DashboardResponse(
        metrics=metrics,
        pipeline=pipeline,
        verticals=[v._asdict() for v in verticals(session)],
        synthetic_trades=int(synthetic),
        open_placeholders=sum(1 for p in placeholders if p.status is PlaceholderStatus.OPEN),
    )


# ------------------------------------------------------------------ buy list


@router.get("/opportunities")
def read_opportunities(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    min_net_pence: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    """The current buy list, best capital velocity first.

    Filtering happens here rather than in the browser so the ranking the buy side
    computed is the ranking shown; re-sorting client-side is how a display quietly
    becomes a second opinion.
    """
    rows = top_opportunities(session, limit=limit)
    return [
        {
            "id": opportunity.id,
            "title": listing.title_norm or "",
            "brand": listing.brand_norm or "",
            "size": listing.size_norm,
            "venue": listing.venue,
            "url": listing.url,
            "price_pence": listing.price_pence,
            "net_pence": opportunity.net_pence,
            "roi": opportunity.roi,
            "capital_velocity": opportunity.capital_velocity,
            "est_confidence": opportunity.est_confidence,
            "match_confidence": opportunity.match_confidence,
            "comp_n": opportunity.comp_n,
            "favourites": listing.favourites,
            "views": listing.views,
            "fee_table_version": opportunity.fee_table_version,
        }
        for opportunity, listing in rows
        if opportunity.net_pence >= min_net_pence
    ]


@router.get("/skip-reasons")
def read_skip_reasons() -> list[str]:
    return list(SKIP_REASONS)


@router.post("/decisions")
def create_decision(request: DecisionRequest, session: SessionDep) -> dict[str, object]:
    """Record a buy or a skip. Refuses a reasonless skip, as the CLI does.

    The refusal is not form validation. AutoBuy's dry-run scores itself by replaying
    candidates and diffing against these rows; without reasons it has nothing to score
    against, and the comparison flatters the automation by default.
    """
    outcome = DecisionOutcome(request.outcome)
    if outcome is DecisionOutcome.SKIPPED and request.skip_reason not in SKIP_REASONS:
        raise HTTPException(
            status_code=422,
            detail=f"a skip needs a reason from {list(SKIP_REASONS)}",
        )
    if outcome is DecisionOutcome.BOUGHT and request.spend is None:
        raise HTTPException(
            status_code=422,
            detail="a purchase needs a spend: without a cost basis every downstream "
            "margin is overstated",
        )
    try:
        decision = Decision(
            opportunity_id=request.opportunity_id,
            mode=DecisionMode.MANUAL,
            outcome=outcome,
            skip_reason=request.skip_reason if outcome is DecisionOutcome.SKIPPED else None,
            decided_at=utcnow(),
            spend_pence=parse_pence(request.spend),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"id": record_decision(session, decision)}


# ------------------------------------------------------------------ inventory


@router.get("/inventory")
def read_inventory(
    session: SessionDep,
    state: Annotated[str | None, Query()] = None,
) -> list[dict[str, object]]:
    """Owned stock, optionally filtered by lifecycle state."""
    stmt = select(Inventory).order_by(Inventory.acquired_at.desc())
    if state:
        stmt = stmt.where(Inventory.state == state)
    now = utcnow()
    return [
        {
            "id": row.id,
            "state": row.state,
            "cost_pence": row.cost_pence,
            "gross_pence": row.gross_pence,
            "acquired_at": row.acquired_at.isoformat(),
            "sold_at": row.sold_at.isoformat() if row.sold_at else None,
            "age_days": (now - row.acquired_at).days,
            "settled": row.actual_fees_pence is not None,
            "synthetic": row.synthetic,
        }
        for row in session.scalars(stmt).all()
    ]


@router.get("/hazards")
def read_hazards(session: SessionDep) -> dict[str, object]:
    """Anything at risk of being sold twice. A query over state, not an event replay."""
    found = hazards(session)
    return {
        "hazards": [
            {
                "inventory_id": h.inventory_id,
                "venue": h.venue,
                "external_id": h.external_id,
                "kind": h.kind.value,
                "requested_at": h.requested_at.isoformat() if h.requested_at else None,
                "detail": h.detail,
            }
            for h in found
        ],
        "in_flight": len(unresolved_delists(session)),
    }


@router.post("/hazards/{inventory_id}/request-delist")
def create_delist_request(
    inventory_id: int, keep_venue: str, session: SessionDep
) -> dict[str, object]:
    """Mark every other venue's listing for this item as needing to come down.

    **Requests, does not perform.** The venue call fails independently of this, so
    reporting success here would be inventing a confirmation. The row moves to
    `delist_pending` and only a venue's answer moves it further.
    """
    outcome = request_delists(
        session, inventory_id=inventory_id, exclude_venue=keep_venue, now=utcnow()
    )
    return {
        "requested": list(outcome.requested),
        "already_down": outcome.already_down,
        "status": "pending",
        "note": "requested only — a venue must confirm before this is resolved",
    }


@router.get("/own-listings")
def read_own_listings(session: SessionDep) -> list[dict[str, object]]:
    return [
        {
            "id": row.id,
            "inventory_id": row.inventory_id,
            "venue": row.venue,
            "external_id": row.external_id,
            "ask_pence": row.ask_pence,
            "sold_at": row.sold_at.isoformat() if row.sold_at else None,
            "delist_requested_at": (
                row.delist_requested_at.isoformat() if row.delist_requested_at else None
            ),
            "delisted_at": row.delisted_at.isoformat() if row.delisted_at else None,
            "delist_error": row.delist_error,
        }
        for row in session.scalars(select(OwnListings)).all()
    ]


# ------------------------------------------------------------------ books & tax


@router.get("/books")
def read_books(session: SessionDep) -> dict[str, object]:
    """The ledger. **Single-entry**: cost basis against realised proceeds.

    Named accurately in the payload because the frontend labels from it. This is not
    double-entry bookkeeping and presenting it as such would be a false claim about
    the books, misleading precisely the person relying on them.
    """
    fees = _fees()
    trades = ledger(session, fees)
    settled_net, estimated_net, settled_count = totals(trades)
    return {
        "basis": "single-entry cost basis",
        "settled_net_pence": settled_net,
        "estimated_net_pence": estimated_net,
        "settled_count": settled_count,
        "estimated_count": len(trades) - settled_count,
        "never_summed": True,
        "trades": [
            {
                "inventory_id": t.inventory_id,
                "cost_pence": t.cost_pence,
                "gross_pence": t.gross_pence,
                "fees_pence": t.fees_pence,
                "ship_pence": t.ship_pence,
                "net_pence": t.net_pence,
                "roi": t.roi,
                "days_held": t.days_held,
                "settled": t.settled,
            }
            for t in trades
        ],
    }


@router.get("/tax")
def read_tax(session: SessionDep, year: Annotated[int | None, Query()] = None) -> dict[str, object]:
    """A UK tax year totalled. **No liability figure, deliberately.**

    Tax owed needs other income, personal allowance, Scottish rates and a National
    Insurance position, none of which this system holds. Publishing an estimate from a
    quarter of the inputs would be a confident number about a legal obligation.
    """
    fees = _fees()
    target = TaxYear(year) if year is not None else tax_year_of(utcnow())
    summary = summarise_tax_year(session, target, fees)
    return {
        "label": target.label,
        "starts": target.starts.date().isoformat(),
        "ends": target.ends.date().isoformat(),
        "gross_income_pence": summary.gross_income_pence,
        "allowable_costs_pence": summary.allowable_costs_pence,
        "profit_actual_expenses_pence": summary.profit_actual_expenses_pence,
        "profit_trading_allowance_pence": summary.profit_trading_allowance_pence,
        "trading_allowance_pence": TRADING_ALLOWANCE_PENCE,
        "lower_method": summary.lower_method,
        "below_threshold": summary.below_threshold,
        "register_by": target.register_by,
        "sales_count": summary.sales_count,
        "straddling_count": summary.straddling_count,
        "figures_are_provisional": summary.figures_are_provisional,
        "computes_liability": False,
        "disclaimer": (
            "Figures to check with an accountant. Not a filing, not tax advice, and "
            "deliberately no liability estimate — that needs your other income, "
            "personal allowance and National Insurance position."
        ),
    }


# ------------------------------------------------------------------ provenance


@router.get("/provenance")
def read_provenance(session: SessionDep) -> list[dict[str, object]]:
    """The placeholder register against live state."""
    return [
        {
            "id": entry.placeholder.id,
            "gap": entry.placeholder.gap,
            "standing_in": entry.placeholder.standing_in,
            "closed_by": entry.placeholder.closed_by,
            "blast_radius": entry.placeholder.blast_radius,
            "status": entry.status.value,
            "evidence": entry.evidence,
        }
        for entry in resolve(gather(session, FEE_TABLE_DIR))
    ]


@router.get("/monitors/{name}/health")
def read_monitor_health(name: str, session: SessionDep) -> dict[str, object]:
    health = monitor_health(session, name, now=utcnow())
    return {
        "monitor": health.monitor,
        "last_success": health.last_success.isoformat() if health.last_success else None,
        "last_status": health.last_status.value if health.last_status else None,
        "consecutive_failures": health.consecutive_failures,
        "stale": health.stale,
    }


@router.get("/reconcile/preview")
def read_reconcile_preview(session: SessionDep) -> dict[str, object]:
    """Predicted versus actual fees, without writing anything.

    Preview is the default and writing is a separate, explicit act. `--write` rewrites
    a content-hashed table and bumps `fee_table_version`, which makes every score
    computed under the old version non-comparable until it is re-scored. That is not a
    one-click operation.
    """
    del session
    fees = _fees()
    return {
        "fee_table_version": fees.version,
        "provisional": fees.provisional,
        "components": [
            {
                "name": c.name,
                "kind": c.kind.value,
                "scope": c.scope.value,
                "rate": str(c.rate) if c.rate is not None else None,
                "amount_pence": c.amount_pence,
            }
            for c in fees.components
        ],
        "write_requires_confirmation": True,
        "note": (
            "Settlement data is supplied via `arb reconcile-fees --transactions`. "
            "Writing bumps fee_table_version and requires re-scoring."
        ),
    }


@router.get("/health")
def read_health() -> dict[str, object]:
    settings = get_settings()
    return {
        "ok": True,
        "db": str(settings.db_path),
        "freshness_days": settings.comps_freshness_days,
    }
