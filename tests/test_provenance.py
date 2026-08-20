"""The placeholder register, resolved against live state.

Placeholder discipline is four rules: declared, versioned, stamped, listed. The first
three exist already -- `provisional: true` in the fee YAML, the content hash, and
`fee_table_version` on every opportunity. This module is the fourth, and these tests
pin the property that makes it worth having: **it must never report a placeholder as
closed without positive evidence.** A register that flatters the system is worse than
no register, because it turns a known unknown into an unknown one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from arb import provenance
from arb.db import Decisions, Inventory, Opportunities
from arb.provenance import (
    REGISTER,
    LiveState,
    PlaceholderState,
    PlaceholderStatus,
    gather,
    resolve,
)
from arb.sourcing.rank import VelocityPolicy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

T0 = datetime(2026, 8, 20, tzinfo=UTC)
FEE_DIR = Path(__file__).resolve().parent.parent / "src" / "arb" / "data" / "fees"

EMPTY = LiveState(
    provisional_fee_tables=("ebay_uk", "vinted_uk"),
    verified_fee_tables=(),
    sold_obs_total=0,
    sold_obs_with_days=0,
    real_decisions=0,
    realised_sales=0,
    settled_sales=0,
    measured_shipments=0,
    velocity_policy=VelocityPolicy.EXCLUDE,
    ship_in_pence=0,
    ship_out_pence=320,
    contest_provisional=True,
    contest_version="contest-v0",
    fee_versions_in_use=(),
)
"""A system that has measured nothing. Every placeholder is open against it."""


def _status(states: tuple[PlaceholderState, ...]) -> dict[str, PlaceholderStatus]:
    return {state.placeholder.id: state.status for state in states}


def _evidence(states: tuple[PlaceholderState, ...]) -> dict[str, str]:
    return {state.placeholder.id: state.evidence for state in states}


# ---------------------------------------------------------------- the register itself


def test_register_ids_are_unique() -> None:
    ids = [p.id for p in REGISTER]
    assert len(ids) == len(set(ids))


def test_every_placeholder_declares_what_closes_it() -> None:
    """A placeholder with no route to a measurement is not a placeholder. It is a
    permanent guess wearing a better name."""
    for placeholder in REGISTER:
        assert placeholder.closed_by.strip()
        assert placeholder.blast_radius.strip()


def test_resolve_covers_every_registered_placeholder() -> None:
    """No placeholder may be silently unresolvable -- that is how one disappears."""
    resolved = resolve(EMPTY)
    assert len(resolved) == len(REGISTER)
    assert {state.placeholder.id for state in resolved} == {p.id for p in REGISTER}


def test_every_resolution_carries_evidence() -> None:
    for state in resolve(EMPTY):
        assert state.evidence.strip(), f"{state.placeholder.id} resolved without evidence"


# ---------------------------------------------------------------- the safety property


def test_an_empty_system_reports_nothing_closed() -> None:
    """The most important test here. A fresh database has measured nothing, so
    nothing may be reported as measured."""
    for state in resolve(EMPTY):
        assert state.status is not PlaceholderStatus.CLOSED


# ---------------------------------------------------------------- P1, fees


def test_fees_are_open_while_any_table_is_provisional() -> None:
    state = EMPTY._replace(provisional_fee_tables=("ebay_uk",))
    assert _status(resolve(state))["P1"] is PlaceholderStatus.OPEN


def test_fees_close_only_when_every_table_is_verified() -> None:
    state = EMPTY._replace(provisional_fee_tables=(), verified_fee_tables=("ebay_uk", "vinted_uk"))
    assert _status(resolve(state))["P1"] is PlaceholderStatus.CLOSED


def test_a_verified_table_does_not_close_fees_while_another_is_provisional() -> None:
    """Selling on two venues under one verified table and one guess still means half
    the margins are fiction."""
    state = EMPTY._replace(provisional_fee_tables=("vinted_uk",), verified_fee_tables=("ebay_uk",))
    assert _status(resolve(state))["P1"] is PlaceholderStatus.OPEN


def test_no_fee_tables_at_all_is_unknown_not_closed() -> None:
    """Nothing to check is not the same as nothing to worry about. An empty fee
    directory satisfies 'no table is provisional' and would otherwise read as green."""
    state = EMPTY._replace(provisional_fee_tables=(), verified_fee_tables=())
    assert _status(resolve(state))["P1"] is PlaceholderStatus.UNKNOWN


# ---------------------------------------------------------------- P2, days to sell


def test_velocity_is_open_with_no_observed_days_to_sell() -> None:
    state = EMPTY._replace(sold_obs_total=500, sold_obs_with_days=0)
    assert _status(resolve(state))["P2"] is PlaceholderStatus.OPEN


def test_velocity_closes_when_days_are_observed_and_not_assumed() -> None:
    state = EMPTY._replace(
        sold_obs_total=500, sold_obs_with_days=400, velocity_policy=VelocityPolicy.EXCLUDE
    )
    assert _status(resolve(state))["P2"] is PlaceholderStatus.CLOSED


def test_assumed_velocity_stays_open_even_with_real_data() -> None:
    """Measured days-to-sell does not help while ranking still substitutes a default
    for the rows that lack it."""
    state = EMPTY._replace(
        sold_obs_total=500,
        sold_obs_with_days=400,
        velocity_policy=VelocityPolicy.ASSUME_DEFAULT,
    )
    assert _status(resolve(state))["P2"] is PlaceholderStatus.OPEN


# ---------------------------------------------------------------- count-driven gaps


@pytest.mark.parametrize(
    ("placeholder_id", "below", "at"),
    [
        ("P3", EMPTY._replace(realised_sales=99), EMPTY._replace(realised_sales=100)),
        ("P4", EMPTY._replace(realised_sales=19), EMPTY._replace(realised_sales=20)),
        ("P5", EMPTY._replace(measured_shipments=9), EMPTY._replace(measured_shipments=10)),
        ("P6", EMPTY._replace(realised_sales=19), EMPTY._replace(realised_sales=20)),
        ("P8", EMPTY._replace(real_decisions=49), EMPTY._replace(real_decisions=50)),
    ],
)
def test_count_driven_placeholders_close_only_at_their_trigger(
    placeholder_id: str, below: LiveState, at: LiveState
) -> None:
    """Triggers come from ROADMAP section 9. Closing one early is the failure mode
    this whole module exists to prevent."""
    assert _status(resolve(below))[placeholder_id] is PlaceholderStatus.OPEN
    assert _status(resolve(at))[placeholder_id] is PlaceholderStatus.CLOSED


def test_evidence_shows_progress_toward_the_trigger() -> None:
    """'12 of 50' is actionable in a way that 'open' is not."""
    evidence = _evidence(resolve(EMPTY._replace(real_decisions=12)))["P8"]
    assert "12" in evidence
    assert "50" in evidence


# ---------------------------------------------------------------- P7, ledger


def test_ledger_is_open_until_a_sale_settles() -> None:
    assert _status(resolve(EMPTY))["P7"] is PlaceholderStatus.OPEN


def test_ledger_closes_on_the_first_settled_sale() -> None:
    state = EMPTY._replace(settled_sales=1)
    assert _status(resolve(state))["P7"] is PlaceholderStatus.CLOSED


# ---------------------------------------------------------------- P9, contest


def test_contest_thresholds_are_registered_as_a_placeholder() -> None:
    """The contest filter shipped with invented thresholds. Declaring it here is the
    difference between a placeholder and a lie."""
    assert "P9" in {p.id for p in REGISTER}
    assert _status(resolve(EMPTY))["P9"] is PlaceholderStatus.OPEN


def test_contest_closes_when_the_policy_stops_being_provisional() -> None:
    state = EMPTY._replace(contest_provisional=False)
    assert _status(resolve(state))["P9"] is PlaceholderStatus.CLOSED


def test_contest_evidence_names_the_policy_version() -> None:
    """A retune must be visible. Without the version two different threshold sets
    produce identical reports."""
    state = EMPTY._replace(contest_version="contest-v7")
    assert "contest-v7" in _evidence(resolve(state))["P9"]


# ---------------------------------------------------------------- gather, against a db


def test_gather_on_an_empty_database_reports_nothing_measured(
    session: Session, tmp_path: Path
) -> None:
    state = gather(session, tmp_path)
    assert state.real_decisions == 0
    assert state.realised_sales == 0
    assert state.fee_versions_in_use == ()


def test_gather_reads_the_shipped_fee_tables(session: Session) -> None:
    """The real tables ship provisional. If this fails, someone lifted the flag."""
    state = gather(session, FEE_DIR)
    assert set(state.provisional_fee_tables) == {"ebay_uk", "vinted_uk"}
    assert state.verified_fee_tables == ()


def test_gather_counts_only_real_decisions(session: Session, tmp_path: Path) -> None:
    """Dry-run decisions are the thing being validated, so counting them as evidence
    that validation is possible would be circular."""
    session.add(Decisions(mode="manual", outcome="skipped", skip_reason="x", decided_at=T0))
    session.add(Decisions(mode="dryrun", outcome="skipped", skip_reason="x", decided_at=T0))
    session.flush()
    assert gather(session, tmp_path).real_decisions == 1


def test_gather_counts_realised_and_settled_sales_separately(
    session: Session, tmp_path: Path
) -> None:
    """A sold item and an item whose fees came back from settlement are different
    evidence: only the second can correct the fee table."""
    session.add(Inventory(cost_pence=1000, acquired_at=T0, sold_at=T0))
    session.add(Inventory(cost_pence=1000, acquired_at=T0, sold_at=T0, actual_fees_pence=140))
    session.flush()
    state = gather(session, tmp_path)
    assert state.realised_sales == 2
    assert state.settled_sales == 1


def test_gather_reports_which_fee_versions_scored_the_book(
    session: Session, tmp_path: Path
) -> None:
    """This is what `fee_table_version` was stamped for: after a correction you need
    to find every score computed under the old assumption."""
    for version, count in (("ebay_uk@aaa", 2), ("ebay_uk@bbb", 1)):
        for _ in range(count):
            session.add(
                Opportunities(
                    est_p25_pence=1000,
                    est_p60_pence=1200,
                    comp_n=5,
                    est_confidence=0.5,
                    match_confidence=0.5,
                    fees_pence=100,
                    ship_in_pence=0,
                    ship_out_pence=320,
                    net_pence=200,
                    roi=0.2,
                    fee_table_version=version,
                    scored_at=T0,
                )
            )
    session.flush()
    counts = dict(gather(session, tmp_path).fee_versions_in_use)
    assert counts == {"ebay_uk@aaa": 2, "ebay_uk@bbb": 1}


def test_a_registered_placeholder_without_a_resolver_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silently skipping a registered gap is the exact failure this module prevents,
    so it raises rather than returning a shorter list nobody counts."""
    monkeypatch.setattr(provenance, "_RESOLVERS", {})
    with pytest.raises(RuntimeError, match="no resolver"):
        resolve(EMPTY)


def test_gather_treats_a_missing_fee_directory_as_unknown(session: Session, tmp_path: Path) -> None:
    """No directory is not the same as no provisional tables. Returning empty tuples
    routes P1 to UNKNOWN rather than letting it read as verified."""
    state = gather(session, tmp_path / "nonexistent")
    assert state.provisional_fee_tables == ()
    assert state.verified_fee_tables == ()
    assert _status(resolve(state))["P1"] is PlaceholderStatus.UNKNOWN
