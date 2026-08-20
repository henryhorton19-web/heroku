"""CLI smoke tests. Every command runs against a temporary database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from arb import __version__
from arb.cli import _report_scan, app
from arb.comps.service import CompsResult
from arb.config import get_settings
from arb.models import Attributes, Listing, Opportunity, Valuation, Venue
from arb.provenance import REGISTER
from arb.repo import upsert_listing, write_opportunity
from arb.sourcing.rank import ScanResult
from arb.sourcing.scanner import RejectedListing, ScanOutcome
from arb.store import make_engine, session_scope
from tests.conftest import FIXTURES

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point settings at a temp DB and clear the cache so no test sees a real .env."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARB_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("ARB_EBAY_REST_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.delenv("ARB_SOLDCOMPS_API_KEY", raising=False)
    monkeypatch.delenv("ARB_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ARB_APIFY_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "load-refdata" in result.output
    assert "doctor" in result.output


def test_db_upgrade_then_current() -> None:
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    result = runner.invoke(app, ["db", "current"])
    assert result.exit_code == 0


def test_db_upgrade_is_idempotent() -> None:
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0


def test_doctor_runs_with_no_credentials() -> None:
    """The scaffold must be usable before any API key exists."""
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "not set" in result.output
    assert "refdata     0 rows" in result.output


def test_doctor_reports_loaded_refdata() -> None:
    runner.invoke(app, ["db", "upgrade"])
    runner.invoke(app, ["load-refdata", "--data-dir", str(FIXTURES / "vinted_ref")])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "refdata     0 rows" not in result.output
    assert "brands)" in result.output


def test_load_refdata_reports_counts() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["load-refdata", "--data-dir", str(FIXTURES / "vinted_ref")])
    assert result.exit_code == 0
    assert "brand" in result.output
    assert "catalog" in result.output


def test_load_refdata_missing_directory_exits_two() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["load-refdata", "--data-dir", "/nonexistent/path"])
    assert result.exit_code == 2


def test_load_refdata_empty_directory_exits_one(tmp_path: Path) -> None:
    runner.invoke(app, ["db", "upgrade"])
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["load-refdata", "--data-dir", str(empty)])
    assert result.exit_code == 1


def test_doctor_exits_one_when_the_database_is_unmigrated() -> None:
    """`doctor` reports rather than fixes, but an unusable database is fatal --
    every other command depends on it."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "UNUSABLE" in result.output


def _seeded_opportunity_id() -> int:
    """Migrate a temp DB and put one scored opportunity in it."""
    when = datetime(2026, 8, 1, tzinfo=UTC)
    engine = make_engine(get_settings().db_url)
    with session_scope(engine) as session:
        listing_id = upsert_listing(
            session,
            Listing(
                venue=Venue.VINTED,
                external_id="1",
                price_pence=1200,
                attrs=Attributes(brand_norm="nike", title_norm="nike air max 90", size_norm="M"),
                first_seen=when,
                last_seen=when,
            ),
        )
        return write_opportunity(
            session,
            Opportunity(
                listing_id=0,
                valuation=Valuation(
                    est_p25_pence=4500,
                    est_p60_pence=5000,
                    comp_n=8,
                    est_confidence=0.5,
                    match_confidence=0.9,
                    days_to_sell_p50=7,
                ),
                fees_pence=600,
                ship_in_pence=0,
                ship_out_pence=300,
                net_pence=2400,
                roi=2.0,
                capital_velocity=0.05,
                fee_table_version="ebay_uk@abc123def456",
                scored_at=when,
            ),
            listing_id=listing_id,
        )


def test_buylist_is_empty_before_scanning() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["buylist"])
    assert result.exit_code == 0
    assert "run `arb scan`" in result.output


def test_buylist_shows_a_scored_opportunity() -> None:
    runner.invoke(app, ["db", "upgrade"])
    opportunity_id = _seeded_opportunity_id()
    result = runner.invoke(app, ["buylist"])
    assert result.exit_code == 0
    assert str(opportunity_id) in result.output
    assert "24.00" in result.output


def test_decide_refuses_a_skip_without_a_reason() -> None:
    """The rule that makes AutoBuy's dry-run scoreable, enforced at the command line."""
    runner.invoke(app, ["db", "upgrade"])
    opportunity_id = _seeded_opportunity_id()
    result = runner.invoke(app, ["decide", str(opportunity_id), "--outcome", "skipped"])
    assert result.exit_code == 2
    assert "skip_reason is required" in result.output


def test_decide_records_a_skip_with_a_reason() -> None:
    runner.invoke(app, ["db", "upgrade"])
    opportunity_id = _seeded_opportunity_id()
    result = runner.invoke(
        app,
        ["decide", str(opportunity_id), "--outcome", "skipped", "--reason", "photos too dark"],
    )
    assert result.exit_code == 0
    assert "skipped" in result.output


def test_decide_refuses_a_purchase_without_a_cost_basis() -> None:
    """A buy recorded at zero would overstate every downstream margin."""
    runner.invoke(app, ["db", "upgrade"])
    opportunity_id = _seeded_opportunity_id()
    result = runner.invoke(app, ["decide", str(opportunity_id), "--outcome", "bought"])
    assert result.exit_code == 2
    assert "--spend is required" in result.output


def test_decide_records_a_purchase() -> None:
    runner.invoke(app, ["db", "upgrade"])
    opportunity_id = _seeded_opportunity_id()
    result = runner.invoke(
        app, ["decide", str(opportunity_id), "--outcome", "bought", "--spend", "11.50"]
    )
    assert result.exit_code == 0
    assert "bought" in result.output


def test_decide_rejects_an_unknown_opportunity() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(
        app, ["decide", "99999", "--outcome", "skipped", "--reason", "does not exist"]
    )
    assert result.exit_code == 2
    assert "no opportunity" in result.output


def test_scan_refuses_without_a_comps_key() -> None:
    """Fail fast and say why, rather than fetching listings it cannot price."""
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["scan", "nike air max"])
    assert result.exit_code == 2
    assert "ARB_SOLDCOMPS_API_KEY" in result.output


def test_scan_refuses_an_unknown_fee_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARB_SOLDCOMPS_API_KEY", "sc_test")
    get_settings.cache_clear()
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["scan", "nike", "--fee-table", "nonexistent_venue"])
    assert result.exit_code == 2
    assert "no fee table" in result.output


def test_scan_report_distinguishes_the_reasons_a_list_is_empty() -> None:
    """A bare empty list cannot tell you whether the market was quiet, nothing could
    be priced, or the comps quota ran out. The counts can."""
    listing = Listing(
        venue=Venue.VINTED,
        external_id="1",
        price_pence=1200,
        attrs=Attributes(brand_norm="nike", title_norm="nike hoodie", size_norm="M"),
        first_seen=datetime(2026, 8, 1, tzinfo=UTC),
        last_seen=datetime(2026, 8, 1, tzinfo=UTC),
    )
    outcome = ScanOutcome(
        result=ScanResult(
            ranked=(),
            suppressed_unknown_velocity=4,
            suppressed_below_floor=2,
            suppressed_anomalous_cost=1,
        ),
        rejected_quality=(RejectedListing(listing, "quality:damage"),),
        unpriceable=(RejectedListing(listing, "no_valuation:comp_floor"),),
    )

    _report_scan(outcome, CompsResult(cache_hits=9, fetches=1, quota_exhausted=True))
    assert outcome.result.suppressed_unknown_velocity == 4


def test_scan_report_warns_when_the_quota_ran_out(capsys: pytest.CaptureFixture[str]) -> None:
    _report_scan(
        ScanOutcome(
            result=ScanResult(ranked=(), suppressed_unknown_velocity=0, suppressed_below_floor=0),
            rejected_quality=(),
            unpriceable=(),
        ),
        CompsResult(cache_hits=0, fetches=3, quota_exhausted=True),
    )
    captured = capsys.readouterr()
    assert "quota exhausted" in captured.err
    assert "incomplete" in captured.err
    assert "nothing to buy" in captured.out


# ------------------------------------------------- provenance (added with provenance.py)


def test_provenance_lists_every_placeholder() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["provenance"])
    assert result.exit_code == 0
    for placeholder in REGISTER:
        assert placeholder.id in result.output


def test_provenance_reports_a_fresh_system_as_entirely_open() -> None:
    """Nothing has been measured, so nothing may read as measured."""
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["provenance"])
    assert f"{len(REGISTER)} open" in result.output
    assert "0 closed" in result.output


def test_provenance_shows_the_evidence_not_just_the_status() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["provenance"])
    assert "provisional" in result.output


def test_provenance_warns_when_the_book_spans_two_fee_versions() -> None:
    """Two fee versions means two sets of assumptions in one book, so the margins
    are not comparable with each other. That needs a re-score, and saying so is the
    entire reason `fee_table_version` is stamped."""
    runner.invoke(app, ["db", "upgrade"])
    settings = get_settings()
    engine = make_engine(settings.db_url)
    listing = Listing(
        venue=Venue.VINTED,
        external_id="1",
        price_pence=1200,
        attrs=Attributes(brand_norm="nike", title_norm="nike hoodie"),
        first_seen=datetime(2026, 8, 1, tzinfo=UTC),
        last_seen=datetime(2026, 8, 1, tzinfo=UTC),
    )
    with session_scope(engine) as session:
        listing_id = upsert_listing(session, listing)
        for version in ("ebay_uk@aaaaaaaaaaaa", "ebay_uk@bbbbbbbbbbbb"):
            write_opportunity(
                session,
                Opportunity(
                    listing_id=listing_id,
                    valuation=Valuation(
                        est_p25_pence=5000,
                        est_p60_pence=6000,
                        comp_n=7,
                        est_confidence=0.8,
                        match_confidence=0.8,
                    ),
                    fees_pence=700,
                    ship_in_pence=0,
                    ship_out_pence=320,
                    net_pence=2780,
                    roi=2.3,
                    fee_table_version=version,
                    scored_at=datetime(2026, 8, 1, tzinfo=UTC),
                ),
                listing_id=listing_id,
            )

    result = runner.invoke(app, ["provenance"])
    assert result.exit_code == 0
    assert "ebay_uk@aaaaaaaaaaaa" in result.output
    assert "re-score" in result.output


def test_provenance_does_not_warn_on_a_single_fee_version() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["provenance"])
    assert "re-score" not in result.output


def test_scan_report_counts_contested_listings() -> None:
    """A scan losing everything to contest is a signal to search a thinner niche,
    which is invisible if contest rejections are pooled with quality ones."""
    listing = Listing(
        venue=Venue.VINTED,
        external_id="1",
        price_pence=1200,
        attrs=Attributes(brand_norm="nike", title_norm="nike hoodie"),
        first_seen=datetime(2026, 8, 1, tzinfo=UTC),
        last_seen=datetime(2026, 8, 1, tzinfo=UTC),
    )
    _report_scan(
        ScanOutcome(
            result=ScanResult(ranked=(), suppressed_unknown_velocity=0, suppressed_below_floor=0),
            rejected_quality=(),
            unpriceable=(),
            rejected_contest=(RejectedListing(listing, "contest:high_save_rate"),),
        ),
        CompsResult(cache_hits=1, fetches=0, quota_exhausted=False),
    )
