"""Comp matching. Blocking and scoring are tested separately because they fail
differently: a blocking failure means wrong product, a low score means same product
described differently."""

from __future__ import annotations

import pytest

from arb.comps.matching import blocks, match_confidence, select_comps
from arb.models import CompQuery, ConditionBand, SoldObservation


def _q(**kw: object) -> CompQuery:
    base: dict[str, object] = {
        "brand_norm": "nike",
        "title_norm": "nike air max 90 white",
        "size_norm": "M",
    }
    base.update(kw)
    return CompQuery.model_validate(base)


def _o(**kw: object) -> SoldObservation:
    base: dict[str, object] = {
        "brand_norm": "nike",
        "title_norm": "nike air max 90 white trainers",
        "size_norm": "M",
        "price_pence": 4500,
    }
    base.update(kw)
    return SoldObservation.model_validate(base)


def test_same_brand_and_size_blocks_through() -> None:
    assert blocks(_q(), _o())


def test_different_brand_is_fatal() -> None:
    assert not blocks(_q(), _o(brand_norm="adidas"))


def test_brand_variants_still_block_through() -> None:
    """Normalisation runs inside blocking, so Levi's and Levis are one brand."""
    assert blocks(_q(brand_norm="Levi's"), _o(brand_norm="Levis", title_norm="levis 501"))


def test_stated_size_disagreement_is_fatal() -> None:
    assert not blocks(_q(size_norm="M"), _o(size_norm="XXL"))


@pytest.mark.parametrize(("q_size", "o_size"), [(None, "M"), ("M", ""), (None, None)])
def test_missing_size_is_compatible_not_a_mismatch(q_size: str | None, o_size: str | None) -> None:
    """Plenty of real listings omit size. Treating absence as mismatch would empty
    the comp set for exactly the items most worth pricing."""
    assert blocks(_q(size_norm=q_size), _o(size_norm=o_size))


def test_stated_condition_disagreement_is_fatal() -> None:
    assert not blocks(
        _q(condition_band=ConditionBand.NEW_WITH_TAGS),
        _o(condition_band=ConditionBand.SATISFACTORY),
    )


def test_confidence_is_higher_for_closer_titles() -> None:
    close = match_confidence(_q(), _o())
    far = match_confidence(_q(), _o(title_norm="nike running socks three pack"))
    assert close > far
    assert 0.0 <= far <= close <= 1.0


def test_select_drops_unrelated_titles() -> None:
    """An unrelated item in the comp set moves the median and the estimate still
    looks perfectly ordinary. That is the failure this threshold exists to stop."""
    kept, conf = select_comps(_q(), [_o(), _o(title_norm="nike gift card 10 pounds")])
    assert len(kept) == 1
    assert conf > 0.7


def test_select_returns_zero_confidence_when_nothing_matches() -> None:
    kept, conf = select_comps(_q(), [_o(brand_norm="adidas")])
    assert kept == []
    assert conf == 0.0


def test_select_confidence_is_the_mean_of_survivors() -> None:
    kept, conf = select_comps(_q(), [_o(), _o()])
    assert len(kept) == 2
    assert conf == pytest.approx(match_confidence(_q(), _o()))
