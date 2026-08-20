"""Reference loader tests, run against trimmed copies of the real upstream payloads.

Fixtures are real captures rather than hand-written JSON, so a shape change upstream
surfaces here instead of at load time.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from arb.db import VintedRef
from arb.refdata import REF_FILES, _parse, load_reference_data
from tests.conftest import FIXTURES

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

REF_DIR = FIXTURES / "vinted_ref"


def _count(session: Session, kind: str) -> int:
    return (
        session.scalar(select(func.count()).select_from(VintedRef).where(VintedRef.kind == kind))
        or 0
    )


def test_loads_every_kind(session: Session) -> None:
    counts = load_reference_data(session, REF_DIR)
    assert set(counts) >= {"brand", "catalog", "colour", "size", "size_group", "status", "country"}
    assert all(v > 0 for v in counts.values())


def test_catalog_tree_is_flattened_with_parents(session: Session) -> None:
    """Catalogs nest four deep upstream (796 nodes across 4 roots). Flattening while
    keeping parent_id is what makes the tree reconstructable without nested JSON."""
    load_reference_data(session, REF_DIR)
    rows = session.scalars(select(VintedRef).where(VintedRef.kind == "catalog")).all()
    assert len(rows) > 2, "children were not walked"
    roots = [r for r in rows if r.parent_id is None]
    children = [r for r in rows if r.parent_id is not None]
    assert roots
    assert children
    ids = {r.external_id for r in rows}
    assert all(c.parent_id in ids for c in children), "orphaned child catalog"


def test_catalog_codes_are_locale_independent(session: Session) -> None:
    """Titles are French; codes are not. Joins must key on code or id."""
    load_reference_data(session, REF_DIR)
    roots = session.scalars(
        select(VintedRef).where(VintedRef.kind == "catalog", VintedRef.parent_id.is_(None))
    ).all()
    assert {r.code for r in roots} >= {"WOMEN_ROOT"}
    assert any(r.title == "Femmes" for r in roots), "fixture should still be FR-locale"


def test_sizes_are_parented_to_their_group(session: Session) -> None:
    load_reference_data(session, REF_DIR)
    groups = {
        r.external_id
        for r in session.scalars(select(VintedRef).where(VintedRef.kind == "size_group"))
    }
    sizes = session.scalars(select(VintedRef).where(VintedRef.kind == "size")).all()
    assert sizes
    assert all(s.parent_id in groups for s in sizes)


def test_size_composites_are_stored_verbatim(session: Session) -> None:
    """`"XS / 34 / 6"` is alpha/EU/UK. Splitting it needs category context, so the
    loader stores it whole and leaves the decision to Step 1."""
    load_reference_data(session, REF_DIR)
    titles = [r.title for r in session.scalars(select(VintedRef).where(VintedRef.kind == "size"))]
    assert any("/" in t for t in titles)


def test_statuses_match_the_condition_band_ids(session: Session) -> None:
    load_reference_data(session, REF_DIR)
    ids = {
        int(r.external_id)
        for r in session.scalars(select(VintedRef).where(VintedRef.kind == "status"))
    }
    assert ids == {1, 2, 3, 4, 6}


def test_titles_are_normalised(session: Session) -> None:
    load_reference_data(session, REF_DIR)
    rows = session.scalars(select(VintedRef)).all()
    assert all(r.title_norm == r.title_norm.strip().casefold() for r in rows)
    assert any(r.title != r.title_norm for r in rows)


def test_load_is_idempotent(session: Session) -> None:
    """Reference data is refreshable, so a second load must upsert rather than
    duplicate or fail."""
    first = load_reference_data(session, REF_DIR)
    before = session.scalar(select(func.count()).select_from(VintedRef))
    second = load_reference_data(session, REF_DIR)
    after = session.scalar(select(func.count()).select_from(VintedRef))
    assert first == second
    assert before == after


def test_missing_files_are_skipped_not_fatal(session: Session, tmp_path: Path) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "color.json").write_text(
        (REF_DIR / "color.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    counts = load_reference_data(session, partial)
    assert set(counts) == {"colour"}


def test_empty_directory_returns_nothing(session: Session, tmp_path: Path) -> None:
    assert load_reference_data(session, tmp_path) == {}


def test_malformed_payload_raises(session: Session, tmp_path: Path) -> None:
    """Fail loudly. A silently half-parsed reference table is worse than none."""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "brand.json").write_text(json.dumps([{"title": "no id here"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="validation error"):
        load_reference_data(session, bad)


def test_ref_files_cover_the_kinds_we_expect() -> None:
    assert set(REF_FILES) == {"brand", "catalog", "colour", "size", "status", "country"}
    assert "material.json" not in REF_FILES.values()


def test_brand_seed_is_not_an_allowlist(session: Session) -> None:
    """Documents the constraint in an executable form.

    The upstream brand table is a ~2.5k seed, not a census: Stone Island, Barbour,
    Patagonia, Arc'teryx, Berghaus and Columbia are all absent from it. Any future
    code that filters candidate stock by membership in `vinted_ref` would drop
    exactly the brands worth trading, so this test exists to make that failure mode
    visible to whoever is tempted.
    """
    load_reference_data(session, REF_DIR)
    known = {
        r.title_norm for r in session.scalars(select(VintedRef).where(VintedRef.kind == "brand"))
    }
    assert "barbour" not in known
    assert _count(session, "brand") > 0


def test_unknown_kind_is_rejected() -> None:
    """Adding a file to REF_FILES without a parser must fail loudly, not silently
    load nothing."""
    with pytest.raises(ValueError, match="unknown reference kind: material"):
        _parse("material", [])
