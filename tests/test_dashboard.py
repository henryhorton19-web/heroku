"""Dashboard, verticals and the synthetic seed.

The property that matters most: **seeding cannot close a placeholder.** The dashboard
is developed against generated trades so it has shape before the first real sale, and
the whole arrangement is only safe because those rows are excluded from every count
the provenance register takes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from arb.books.ledger import capital_position, ledger
from arb.books.verticals import seed_synthetic_trades, verticals
from arb.comps.fees import load_fee_table
from arb.dashboard import DashboardData, render_dashboard
from arb.db import Inventory, Listings, Opportunities
from arb.provenance import PlaceholderStatus, gather, resolve

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FEES = load_fee_table(Path(__file__).resolve().parent.parent / "src/arb/data/fees/ebay_uk.yaml")
FEE_DIR = Path(__file__).resolve().parent.parent / "src" / "arb" / "data" / "fees"
NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _data(session: Session, *, synthetic: int = 0) -> DashboardData:
    return DashboardData(
        generated_at=NOW,
        capital=capital_position(session, now=NOW),
        trades=ledger(session, FEES),
        placeholders=resolve(gather(session, FEE_DIR)),
        verticals=verticals(session),
        synthetic_trades=synthetic,
    )


# ---------------------------------------------------------------- the seed


def test_seeding_produces_trades(session: Session) -> None:
    assert seed_synthetic_trades(session, now=NOW, count=20) == 20
    assert len(ledger(session, FEES)) > 0


def test_every_seeded_row_is_marked_synthetic(session: Session) -> None:
    seed_synthetic_trades(session, now=NOW, count=10)
    rows = session.scalars(select(Inventory)).all()
    assert rows
    assert all(row.synthetic for row in rows)


def test_seeding_cannot_close_a_placeholder(session: Session) -> None:
    """The load-bearing test in this file. If generated data could close P7, the
    register would be lying, which is the one thing it exists not to do."""
    seed_synthetic_trades(session, now=NOW, count=60)
    statuses = {e.placeholder.id: e.status for e in resolve(gather(session, FEE_DIR))}
    assert statuses["P7"] is PlaceholderStatus.OPEN
    assert statuses["P3"] is PlaceholderStatus.OPEN


def test_a_real_settled_sale_still_closes_p7_alongside_seed_data(session: Session) -> None:
    """Seeding must not *mask* real progress either -- excluding synthetic rows has to
    be a filter, not a short circuit."""
    seed_synthetic_trades(session, now=NOW, count=20)
    session.add(
        Inventory(
            cost_pence=1000,
            qty=1,
            state="sold",
            acquired_at=NOW - timedelta(days=5),
            sold_at=NOW,
            gross_pence=3000,
            actual_fees_pence=400,
        )
    )
    session.flush()
    statuses = {e.placeholder.id: e.status for e in resolve(gather(session, FEE_DIR))}
    assert statuses["P7"] is PlaceholderStatus.CLOSED


def test_seeding_is_deterministic(session: Session) -> None:
    """A demo that looks different every run is one you cannot reason about."""
    seed_synthetic_trades(session, now=NOW, count=15)
    first = [row.cost_pence for row in session.scalars(select(Inventory)).all()]
    for row in session.scalars(select(Inventory)).all():
        session.delete(row)
    session.flush()
    seed_synthetic_trades(session, now=NOW, count=15)
    second = [row.cost_pence for row in session.scalars(select(Inventory)).all()]
    assert first == second


# ---------------------------------------------------------------- verticals


def test_verticals_need_no_new_collection(session: Session) -> None:
    """category_id, country and favourites were captured from the first scan for
    exactly this."""
    for i in range(4):
        session.add(
            Listings(
                venue="vinted",
                external_id=f"v{i}",
                price_pence=1200,
                category_id="1904",
                country="GB",
                favourites=10,
                first_seen=NOW,
                last_seen=NOW,
            )
        )
    session.flush()
    found = verticals(session, min_listings=3)
    assert len(found) == 1
    assert found[0].category_id == "1904"
    assert found[0].avg_contest == 10


def test_a_thin_slice_is_not_reported(session: Session) -> None:
    session.add(
        Listings(
            venue="vinted",
            external_id="v1",
            price_pence=1200,
            category_id="1904",
            first_seen=NOW,
            last_seen=NOW,
        )
    )
    session.flush()
    assert verticals(session, min_listings=3) == []


def test_an_unpriced_vertical_reports_none_not_zero(session: Session) -> None:
    """An unpriced niche and a zero-margin one are different findings."""
    for i in range(3):
        session.add(
            Listings(
                venue="vinted",
                external_id=f"v{i}",
                price_pence=1200,
                category_id="1904",
                first_seen=NOW,
                last_seen=NOW,
            )
        )
    session.flush()
    assert verticals(session, min_listings=3)[0].median_net_pence is None


# ---------------------------------------------------------------- rendering


def test_the_page_renders_on_an_empty_database(session: Session) -> None:
    """An empty screen is an invitation to act, not a crash."""
    html = render_dashboard(_data(session))
    assert "<!doctype html>" in html
    assert "No completed sales yet" in html


def test_settled_and_estimated_are_visually_distinct(session: Session) -> None:
    """ROADMAP section 5: a margin from provisional fees and one from settlement data
    must not look identical on screen."""
    session.add(
        Inventory(
            cost_pence=1000,
            qty=1,
            state="sold",
            acquired_at=NOW,
            sold_at=NOW,
            gross_pence=3000,
            actual_fees_pence=400,
        )
    )
    session.add(
        Inventory(
            cost_pence=1000,
            qty=1,
            state="sold",
            acquired_at=NOW,
            sold_at=NOW,
            gross_pence=3000,
        )
    )
    session.flush()
    html = render_dashboard(_data(session))
    assert 'class="n measured"' in html
    assert 'class="n assumed"' in html
    assert "never added" in html


def test_the_register_is_rendered_not_footnoted(session: Session) -> None:
    html = render_dashboard(_data(session))
    assert "Assumptions still open" in html
    for placeholder_id in ("P1", "P9", "P10"):
        assert f">{placeholder_id}<" in html


def test_synthetic_data_is_announced(session: Session) -> None:
    """A dashboard demonstrating itself with generated data must say so."""
    seed_synthetic_trades(session, now=NOW, count=10)
    html = render_dashboard(_data(session, synthetic=10))
    assert "generated, not traded" in html


def test_no_synthetic_banner_when_there_is_none(session: Session) -> None:
    assert "generated, not traded" not in render_dashboard(_data(session))


def test_content_is_escaped(session: Session) -> None:
    """Category ids come from a marketplace payload; they are not ours to trust."""
    for i in range(3):
        session.add(
            Listings(
                venue="vinted",
                external_id=f"v{i}",
                price_pence=1200,
                category_id="<script>alert(1)</script>",
                first_seen=NOW,
                last_seen=NOW,
            )
        )
    session.flush()
    html = render_dashboard(_data(session))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_page_is_self_contained(session: Session) -> None:
    """No server, no build step, no network. It is a file you open."""
    html = render_dashboard(_data(session))
    assert "<style>" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "<script" not in html


def test_opportunities_feed_the_vertical_median(session: Session) -> None:
    listing = Listings(
        venue="vinted",
        external_id="v1",
        price_pence=1200,
        category_id="1904",
        first_seen=NOW,
        last_seen=NOW,
    )
    session.add(listing)
    session.flush()
    for i in range(3):
        session.add(
            Listings(
                venue="vinted",
                external_id=f"x{i}",
                price_pence=1200,
                category_id="1904",
                first_seen=NOW,
                last_seen=NOW,
            )
        )
    session.add(
        Opportunities(
            listing_id=listing.id,
            est_p25_pence=4000,
            est_p60_pence=5000,
            comp_n=6,
            est_confidence=0.8,
            match_confidence=0.8,
            fees_pence=600,
            ship_in_pence=0,
            ship_out_pence=320,
            net_pence=1500,
            roi=1.2,
            fee_table_version="ebay_uk@test",
            scored_at=NOW,
        )
    )
    session.flush()
    assert verticals(session, min_listings=3)[0].median_net_pence == 1500
