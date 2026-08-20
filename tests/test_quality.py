"""Quality lexicon v0. The negation and word-boundary tests are the important ones --
both failure modes reject exactly the stock worth buying."""

from __future__ import annotations

import pytest

from arb.norm import norm_text
from arb.sourcing.quality import LEXICON, RejectReason, assess


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Nike hoodie, small stain on sleeve", RejectReason.DAMAGE),
        ("Adidas jacket spares or repairs", RejectReason.DAMAGE),
        ("Levis jeans, heavily worn", RejectReason.WEAR),
        ("Ralph Lauren shirt replica", RejectReason.AUTHENTICITY),
        ("Mixed lot of 5 tops job lot", RejectReason.BUNDLE_AMBIGUOUS),
        ("Boys age 5 puffer coat", RejectReason.WRONG_AUDIENCE),
        ("Nike Air Max, box only", RejectReason.INCOMPLETE),
    ],
)
def test_rejects_known_bad_signals(text: str, reason: RejectReason) -> None:
    verdict = assess(text)
    assert not verdict.accepted
    assert reason in verdict.reasons


@pytest.mark.parametrize(
    "text",
    [
        "Nike hoodie, no stains or marks",
        "Adidas jacket, not damaged in any way",
        "Carhartt jacket with no flaws",
        "Barbour wax jacket, free from holes",
        "Stone Island jumper, never worn",
    ],
)
def test_negated_condition_language_is_accepted(text: str) -> None:
    """'no stains' is the language of a GOOD listing. Matching 'stain' naively
    rejects exactly the sellers who bothered to describe condition honestly."""
    assert assess(text).accepted


@pytest.mark.parametrize(
    "text",
    [
        "Nike track top in grease-resistant fabric",
        "Patagonia fleece, marketable classic",
        "The North Face jacket, remarkable condition",
        "Arcteryx shell, holographic logo",
    ],
)
def test_substring_collisions_do_not_reject(text: str) -> None:
    """'grease' contains 'ease', 'remarkable' contains 'mark'. Substring matching
    here would reject half of Vinted."""
    assert assess(text).accepted


def test_clean_listing_is_accepted() -> None:
    verdict = assess("Stone Island jumper size L navy")
    assert verdict.accepted
    assert verdict.reasons == ()
    assert verdict.skip_reason is None


def test_description_is_searched_as_well_as_title() -> None:
    """Sellers routinely put the bad news below the fold."""
    assert not assess("Nike hoodie size M", "Please note there is a small hole").accepted


def test_skip_reason_is_ready_for_the_decisions_table() -> None:
    """Every skip needs a reason, including the automatic ones."""
    reason = assess("bundle of damaged shirts").skip_reason
    assert reason is not None
    assert reason.startswith("quality:")
    assert "damage" in reason


def test_skip_reason_is_deterministic() -> None:
    assert (
        assess("bundle of damaged shirts").skip_reason
        == assess("bundle of damaged shirts").skip_reason
    )


def test_multiple_reasons_are_all_reported() -> None:
    verdict = assess("job lot of kids clothes, some stained")
    assert len(verdict.reasons) >= 2


def test_lexicon_terms_are_all_normalised() -> None:
    """A term with stray case or punctuation would silently never match."""
    for terms in LEXICON.values():
        for term in terms:
            assert term == norm_text(term), term
