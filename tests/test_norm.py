"""Property tests for normalisation.

These are properties rather than examples because `*_norm` values are blocking keys:
a single non-idempotent case silently splits one comp block into two, and the failure
mode is a thinner comp set rather than an error.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arb.norm import (
    _ALPHA_SYNONYMS,
    ALPHA_SIZES,
    norm_brand,
    norm_colour,
    norm_size,
    norm_text,
    strip_accents,
)

ASCII_TEXT = st.text(alphabet=string.ascii_letters + string.digits + " -'./", max_size=40)


@given(st.text(max_size=200))
def test_norm_text_is_idempotent(raw: str) -> None:
    once = norm_text(raw)
    assert norm_text(once) == once


@given(st.text(max_size=200))
def test_norm_text_whitespace_invariants(raw: str) -> None:
    out = norm_text(raw)
    assert out == out.strip()
    assert "  " not in out
    assert "\n" not in out
    assert "\t" not in out


@given(st.text(max_size=200))
def test_norm_text_is_casefolded(raw: str) -> None:
    assert norm_text(raw) == norm_text(raw).casefold()


@given(ASCII_TEXT)
def test_norm_text_ignores_ascii_case(raw: str) -> None:
    """Restricted to ASCII deliberately. Unicode case mapping is not a bijection --
    'ß'.upper() is 'SS' -- so asserting this over arbitrary text would be asserting
    something false about Unicode rather than something true about our code."""
    assert norm_text(raw.upper()) == norm_text(raw.lower())


@given(st.text(max_size=100))
def test_norm_size_is_idempotent(raw: str) -> None:
    once = norm_size(raw)
    assert norm_size(once) == once


@given(st.text(max_size=100))
def test_norm_brand_is_idempotent(raw: str) -> None:
    once = norm_brand(raw)
    assert norm_brand(once) == once


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Très bon état", "tres bon etat"),
        ("  Multiple   Spaces  ", "multiple spaces"),
        ("Nike!!! Air??", "nike air"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_norm_text_examples(raw: str, expected: str) -> None:
    assert norm_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "\u2024",  # ONE DOT LEADER, NFKD-decomposes to "."
        "\uff07",  # FULLWIDTH APOSTROPHE, NFKD-decomposes to "\'"
        "A\u2024P\u2024C\u2024",
    ],
)
def test_norm_brand_folds_typographic_punctuation(raw: str) -> None:
    """Regression: found by the idempotence property, not by hand.

    Stripping punctuation before NFKD folding left a full stop behind that a second
    pass would remove. Phone keyboards emit these characters, so the same brand typed
    two ways would have landed in two different comp blocks."""
    once = norm_brand(raw)
    assert norm_brand(once) == once


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Levi's", "Levis"),
        ("Levi\uff07s", "Levis"),
        ("A\u2024P\u2024C\u2024", "APC"),
        ("Levi\u2019s", "Levis"),
        ("A.P.C.", "APC"),
        ("THE NORTH FACE", "The North Face"),
    ],
)
def test_norm_brand_collapses_variants(left: str, right: str) -> None:
    assert norm_brand(left) == norm_brand(right)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("xs", "XS"),
        ("XS", "XS"),
        ("x-small", "XS"),
        ("Extra Small", "XS"),
        ("medium", "M"),
        ("M", "M"),
        ("xxl", "XXL"),
        ("2XL", "XXL"),
        ("extra extra large", "XXL"),
        ("xxxl", "XXXL"),
        ("3XL", "XXXL"),
    ],
)
def test_norm_size_canonicalises_alpha(raw: str, expected: str) -> None:
    assert norm_size(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "XS / 34 / 6",  # Vinted composite: alpha / EU / UK
        "80B",  # bra
        "35.5",  # EU shoe
        "9-18 kg",  # car seat
        "Queen Size (150-180 cm x 190-200 cm)",
    ],
)
def test_norm_size_leaves_unrecognised_forms_unconverted(raw: str) -> None:
    """Precision over recall. An unconverted size is a weaker blocking key; a wrongly
    converted one poisons the comp set, and we would never know."""
    out = norm_size(raw)
    assert out not in ALPHA_SIZES
    assert out == norm_size(out)


def test_every_alpha_size_survives_synonym_expansion() -> None:
    """Guards against shadowing. The synonym table is built by expanding each
    canonical size into its spellings and merging into one dict, so a future size
    whose spellings collide with an existing one would vanish silently -- the merge
    has no collision check by design, this test is the check."""
    assert set(_ALPHA_SYNONYMS.values()) == set(ALPHA_SIZES)


def test_every_synonym_round_trips_through_norm_size() -> None:
    for spelling, canonical in _ALPHA_SYNONYMS.items():
        assert norm_size(spelling) == canonical, spelling


@given(st.text(max_size=100))
def test_strip_accents_preserves_ascii(raw: str) -> None:
    ascii_only = "".join(ch for ch in raw if ch.isascii())
    assert strip_accents(ascii_only) == ascii_only


@given(st.text(max_size=100))
def test_norm_colour_matches_norm_text(raw: str) -> None:
    assert norm_colour(raw) == norm_text(raw)


@pytest.mark.parametrize("raw", ["", "   ", "!!!"])
def test_norm_size_of_empty_input_is_empty(raw: str) -> None:
    assert norm_size(raw) == ""
