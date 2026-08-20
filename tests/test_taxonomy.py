"""The taxonomy compliance gate.

Since August 2026 eBay requires Size and Condition on new fashion listings and
blocks, holds or de-indexes non-standard values. **A listing that publishes but is
not indexed looks like success and sells nothing**, which is why this is a hard gate
rather than a warning: the failure is silent, and you find out weeks later when
nothing has sold.

The fixture is hand-built from the field names read out of `ebay_rest`'s shipped
OpenAPI models (`commerce_taxonomy.models`, `attribute_map`), not from a live
capture. The *shape* is verified; the *values* are representative.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from arb.models import ConditionBand, ListingDraft
from arb.selling.aspects_repo import cached_aspects, cached_categories, store_aspects
from arb.selling.taxonomy import (
    AspectMode,
    CategoryAspects,
    ViolationKind,
    parse_aspects,
    validate_draft,
)
from tests.conftest import FIXTURES

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

T0 = datetime(2026, 8, 20, tzinfo=UTC)

PAYLOAD = json.loads(
    (FIXTURES / "taxonomy" / "aspects_womens_tops.json").read_text(encoding="utf-8")
)
CATEGORY = "53159"


def _aspects() -> CategoryAspects:
    parsed = parse_aspects(PAYLOAD, category_id=CATEGORY)
    assert parsed is not None
    return parsed


def _draft(**overrides: object) -> ListingDraft:
    base: dict[str, object] = {
        "title": "Nike Womens Top Black Medium",
        "description": "Good condition Nike top.",
        "category_id": CATEGORY,
        "price_pence": 1800,
        "size": "M",
        "condition_band": ConditionBand.VERY_GOOD,
        "brand": "Nike",
        "aspects": (("Department", "Women"),),
    }
    base.update(overrides)
    return ListingDraft.model_validate(base)


# ---------------------------------------------------------------- parsing


def test_parse_reads_the_verified_field_names() -> None:
    aspects = _aspects()
    assert aspects.category_id == CATEGORY
    assert aspects.category_tree_id == "3"
    assert {a.name for a in aspects.aspects} >= {"Brand", "Department", "Size", "Colour"}


def test_parse_distinguishes_selection_only_from_free_text() -> None:
    """The distinction is the whole gate: a free-text aspect accepts anything, a
    selection-only aspect accepts nothing outside its enum."""
    by_name = {a.name: a for a in _aspects().aspects}
    assert by_name["Size"].mode is AspectMode.SELECTION_ONLY
    assert by_name["Brand"].mode is AspectMode.FREE_TEXT


def test_parse_carries_required_and_allowed_values() -> None:
    by_name = {a.name: a for a in _aspects().aspects}
    assert by_name["Size"].required is True
    assert by_name["Style"].required is False
    assert "M" in by_name["Size"].allowed_values


def test_parse_returns_none_for_an_uncached_category() -> None:
    """Refusing beats guessing. Validating against another category's enums would
    pass a listing that eBay then holds."""
    assert parse_aspects(PAYLOAD, category_id="99999") is None


def test_parse_survives_a_payload_with_no_category_aspects() -> None:
    assert parse_aspects({"categoryAspects": []}, category_id=CATEGORY) is None


# ---------------------------------------------------------------- the happy path


def test_a_compliant_draft_publishes() -> None:
    verdict = validate_draft(_draft(), _aspects())
    assert verdict.publishable
    assert verdict.violations == ()
    assert verdict.blocking_reason is None


# ---------------------------------------------------------------- required aspects


def test_a_missing_required_aspect_blocks_publish() -> None:
    verdict = validate_draft(_draft(aspects=()), _aspects())
    assert not verdict.publishable
    kinds = {v.kind for v in verdict.violations}
    assert ViolationKind.MISSING_REQUIRED in kinds
    assert any(v.aspect == "Department" for v in verdict.violations)


def test_a_blank_size_is_refused_before_the_gate_sees_it() -> None:
    """Size is required on fashion from August 2026, and `ListingDraft` already
    refuses a blank one by construction -- `str_strip_whitespace` plus `min_length`.
    Asserting it here rather than in the gate records where the invariant actually
    lives: a caller cannot reach `validate_draft` holding a size-less draft, the
    same way `record_decision` cannot be handed a reasonless skip."""
    with pytest.raises(ValidationError):
        _draft(size="   ")


# ---------------------------------------------------------------- enum compliance


def test_a_non_standard_size_blocks_publish() -> None:
    """The exact failure the August 2026 rules introduced. 'Medium' is a perfectly
    sensible thing to type and eBay will not index it."""
    verdict = validate_draft(_draft(size="Medium"), _aspects())
    assert not verdict.publishable
    assert any(
        v.kind is ViolationKind.NOT_IN_ENUM and v.aspect == "Size" for v in verdict.violations
    )


def test_the_violation_names_what_would_have_been_accepted() -> None:
    """A gate that says no without saying what yes looks like just moves the problem."""
    verdict = validate_draft(_draft(size="Medium"), _aspects())
    violation = next(v for v in verdict.violations if v.aspect == "Size")
    assert "M" in violation.allowed


def test_free_text_aspects_accept_anything_within_length() -> None:
    """Brand is FREE_TEXT with a listed enum. The enum is a suggestion there, and
    treating it as a whitelist would reject every brand eBay has not seen."""
    verdict = validate_draft(_draft(brand="Arc'teryx"), _aspects())
    assert verdict.publishable


def test_free_text_over_max_length_blocks_publish() -> None:
    verdict = validate_draft(_draft(brand="x" * 80), _aspects())
    assert not verdict.publishable
    assert any(v.kind is ViolationKind.TOO_LONG for v in verdict.violations)


# ---------------------------------------------------------------- conditional values


def test_a_size_valid_only_for_another_department_blocks_publish() -> None:
    """'10-11 Years' is a real Size enum value, but only when Department is Girls.
    This is why the value constraints are parsed rather than flattened into one set:
    a flat enum would accept a childrenswear size on a womenswear listing."""
    verdict = validate_draft(
        _draft(size="10-11 Years", aspects=(("Department", "Women"),)), _aspects()
    )
    assert not verdict.publishable
    assert any(v.kind is ViolationKind.NOT_APPLICABLE for v in verdict.violations)


def test_the_same_size_passes_under_the_department_it_belongs_to() -> None:
    verdict = validate_draft(
        _draft(size="10-11 Years", aspects=(("Department", "Girls"),)), _aspects()
    )
    assert verdict.publishable


def test_size_type_stays_its_own_aspect() -> None:
    """SCOPE.md: keep Women's / Petite / Plus in their own item specifics rather than
    concatenated into Size. Concatenating produces a non-standard size value, which
    is exactly what gets held."""
    verdict = validate_draft(_draft(size="M Petite"), _aspects())
    assert not verdict.publishable
    passing = validate_draft(
        _draft(size="M", aspects=(("Department", "Women"), ("Size Type", "Petite"))),
        _aspects(),
    )
    assert passing.publishable


# ---------------------------------------------------------------- condition


def test_condition_is_always_required_even_though_the_enums_do_not_say_so() -> None:
    """Condition is not an aspect in the Taxonomy response -- it is a separate field
    on the listing -- so the enum walk cannot enforce it. It is checked explicitly
    because August 2026 requires it on fashion and forgetting it is silent."""
    verdict = validate_draft(_draft(condition_band=None), _aspects())
    assert not verdict.publishable
    assert any(v.aspect == "Condition" for v in verdict.violations)


# ---------------------------------------------------------------- reporting


def test_all_violations_are_reported_not_just_the_first() -> None:
    """One round trip per fix is a bad loop when publishing a hundred items."""
    verdict = validate_draft(_draft(size="Medium", brand="x" * 80, aspects=()), _aspects())
    assert len({v.aspect for v in verdict.violations}) >= 3


def test_blocking_reason_is_a_stable_sorted_string() -> None:
    verdict = validate_draft(_draft(size="Medium"), _aspects())
    assert verdict.blocking_reason is not None
    assert verdict.blocking_reason.startswith("taxonomy:")


def test_soon_required_aspects_are_surfaced_without_blocking() -> None:
    """Colour carries expectedRequiredByDate. Blocking on it today would be wrong;
    saying nothing means the deadline arrives as a wall of held listings."""
    verdict = validate_draft(_draft(), _aspects())
    assert verdict.publishable
    assert any("Colour" in warning for warning in verdict.warnings)


# ---------------------------------------------------------------- the cache


def test_a_cache_miss_returns_none_rather_than_an_empty_aspect_set(session: Session) -> None:
    """An empty aspect set validates everything. A miss must refuse, not pass."""
    assert cached_aspects(session, marketplace_id="EBAY_GB", category_id=CATEGORY) is None


def test_stored_aspects_round_trip(session: Session) -> None:
    store_aspects(session, PAYLOAD, marketplace_id="EBAY_GB", category_id=CATEGORY, fetched_at=T0)
    loaded = cached_aspects(session, marketplace_id="EBAY_GB", category_id=CATEGORY)
    assert loaded is not None
    assert {a.name for a in loaded.aspects} >= {"Size", "Department"}


def test_storing_twice_replaces_rather_than_duplicates(session: Session) -> None:
    """eBay's enums are re-fetchable, so an old copy is not history -- it is a stale
    rule that will hold your listing. Upsert, unlike comps_cache."""
    store_aspects(session, PAYLOAD, marketplace_id="EBAY_GB", category_id=CATEGORY, fetched_at=T0)
    bumped = {**PAYLOAD, "categoryTreeVersion": "129"}
    store_aspects(session, bumped, marketplace_id="EBAY_GB", category_id=CATEGORY, fetched_at=T0)
    assert cached_categories(session, marketplace_id="EBAY_GB") == [(CATEGORY, "129")]


def test_marketplaces_do_not_collide(session: Session) -> None:
    """Enums differ per marketplace. EBAY_GB sizes are not EBAY_US sizes."""
    store_aspects(session, PAYLOAD, marketplace_id="EBAY_GB", category_id=CATEGORY, fetched_at=T0)
    assert cached_aspects(session, marketplace_id="EBAY_US", category_id=CATEGORY) is None


def test_a_cached_category_validates_a_draft_end_to_end(session: Session) -> None:
    store_aspects(session, PAYLOAD, marketplace_id="EBAY_GB", category_id=CATEGORY, fetched_at=T0)
    loaded = cached_aspects(session, marketplace_id="EBAY_GB", category_id=CATEGORY)
    assert loaded is not None
    assert validate_draft(_draft(), loaded).publishable
    assert not validate_draft(_draft(size="Medium"), loaded).publishable
