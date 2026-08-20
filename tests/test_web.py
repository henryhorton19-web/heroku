"""The web console.

Every route is a thin adapter onto a service that is already tested elsewhere, so these
tests check the things only the interface can get wrong: that it refuses what the CLI
refuses, and that it does not claim more than the underlying system claims.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from arb.config import get_settings
from arb.store import make_engine, session_scope, upgrade_to_head
from arb.web.api import SKIP_REASONS, get_session
from arb.web.app import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 20, tzinfo=UTC)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db = tmp_path / "web.db"
    monkeypatch.setenv("ARB_DB_PATH", str(db))
    get_settings.cache_clear()
    upgrade_to_head(f"sqlite+pysqlite:///{db}")
    engine = make_engine(f"sqlite+pysqlite:///{db}")

    def _session() -> Iterator[Session]:
        with session_scope(engine) as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_session] = _session
    with TestClient(application) as test_client:
        yield test_client
    get_settings.cache_clear()


# ---------------------------------------------------------------- shell


def test_the_page_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "trading console" in response.text


def test_static_assets_are_served(client: TestClient) -> None:
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_health_reports_the_database(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["ok"] is True


# ---------------------------------------------------------------- dashboard


def test_the_dashboard_loads_on_an_empty_database(client: TestClient) -> None:
    """An empty console is an invitation to act, not a crash."""
    body = client.get("/api/v1/dashboard").json()
    assert body["metrics"]
    assert body["open_placeholders"] == 10


def test_the_pipeline_has_seven_stages_with_one_derived(client: TestClient) -> None:
    """Six stored, plus funds-cleared which is a fact about the fee record rather than
    a state anyone sets."""
    body = client.get("/api/v1/dashboard").json()
    assert len(body["pipeline"]) == 7
    derived = [s for s in body["pipeline"] if s["derived"]]
    assert [s["key"] for s in derived] == ["funds_cleared"]


def test_metrics_carry_the_measured_flag(client: TestClient) -> None:
    """The frontend colours on this and nothing else, so it must be present."""
    body = client.get("/api/v1/dashboard").json()
    settled = next(m for m in body["metrics"] if "settled" in m["label"])
    estimated = next(m for m in body["metrics"] if "estimated" in m["label"])
    assert settled["measured"] is True
    assert estimated["measured"] is False


# ---------------------------------------------------------------- decisions


def test_a_skip_without_a_reason_is_refused(client: TestClient) -> None:
    """The same refusal the CLI makes. AutoBuy's dry-run diffs against these rows."""
    response = client.post("/api/v1/decisions", json={"opportunity_id": 1, "outcome": "skipped"})
    assert response.status_code == 422


def test_a_skip_reason_outside_the_vocabulary_is_refused(client: TestClient) -> None:
    """Free text cannot be compared with anything. A hundred spellings of 'too dear'
    is not an evaluation set."""
    response = client.post(
        "/api/v1/decisions",
        json={"opportunity_id": 1, "outcome": "skipped", "skip_reason": "meh"},
    )
    assert response.status_code == 422


def test_a_purchase_without_a_spend_is_refused(client: TestClient) -> None:
    response = client.post("/api/v1/decisions", json={"opportunity_id": 1, "outcome": "bought"})
    assert response.status_code == 422
    assert "cost basis" in response.json()["detail"]


def test_the_skip_vocabulary_is_exposed(client: TestClient) -> None:
    assert client.get("/api/v1/skip-reasons").json() == list(SKIP_REASONS)


# ---------------------------------------------------------------- claims


def test_the_ledger_is_labelled_single_entry(client: TestClient) -> None:
    """Calling it double-entry would be a false claim about the books, and the person
    most misled would be the one relying on them."""
    assert client.get("/api/v1/books").json()["basis"] == "single-entry cost basis"


def test_settled_and_estimated_are_never_summed(client: TestClient) -> None:
    body = client.get("/api/v1/books").json()
    assert body["never_summed"] is True
    assert "settled_net_pence" in body
    assert "estimated_net_pence" in body


def test_tax_publishes_no_liability_figure(client: TestClient) -> None:
    """Tax owed needs other income, personal allowance, Scottish rates and an NI
    position, none of which this system holds."""
    body = client.get("/api/v1/tax").json()
    assert body["computes_liability"] is False
    assert not any("liability" in k for k in body if k != "computes_liability")


def test_tax_exposes_both_methods_and_which_is_lower(client: TestClient) -> None:
    body = client.get("/api/v1/tax").json()
    assert "profit_actual_expenses_pence" in body
    assert "profit_trading_allowance_pence" in body
    assert body["lower_method"] in {"actual_expenses", "trading_allowance", "either"}


def test_reconciliation_requires_confirmation(client: TestClient) -> None:
    """Writing bumps fee_table_version and invalidates comparability. Not one click."""
    body = client.get("/api/v1/reconcile/preview").json()
    assert body["write_requires_confirmation"] is True
    assert body["provisional"] is True


# ---------------------------------------------------------------- provenance


def test_provenance_exposes_all_ten_placeholders(client: TestClient) -> None:
    rows = client.get("/api/v1/provenance").json()
    assert len(rows) == 10
    assert {r["id"] for r in rows} >= {"P1", "P9", "P10"}
    assert all(r["status"] == "open" for r in rows)


def test_every_placeholder_carries_its_blast_radius(client: TestClient) -> None:
    """Status alone is not actionable; what breaks if it is wrong decides urgency."""
    assert all(r["blast_radius"] for r in client.get("/api/v1/provenance").json())


# ---------------------------------------------------------------- hazards


def test_hazards_are_empty_on_a_clean_book(client: TestClient) -> None:
    body = client.get("/api/v1/hazards").json()
    assert body["hazards"] == []


def test_a_delist_is_requested_not_reported_as_done(client: TestClient) -> None:
    """The venue call fails independently. Reporting success here would be inventing
    a confirmation."""
    response = client.post("/api/v1/hazards/1/request-delist?keep_venue=ebay")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_inventory_filters_by_state(client: TestClient) -> None:
    assert client.get("/api/v1/inventory?state=listed").json() == []


def test_monitor_health_is_reachable(client: TestClient) -> None:
    body = client.get("/api/v1/monitors/nike/health").json()
    assert body["stale"] is True
