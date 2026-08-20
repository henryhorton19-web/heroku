"""Deterministic normalisation of free text into blocking keys.

Every `*_norm` column in the schema is produced here. Matching quality depends on
these being stable and idempotent, so they are property-tested rather than
example-tested.

Scope note: this module normalises *form*, never *meaning*. It will not convert
between sizing systems (alpha vs EU vs UK). Vinted's own size labels are
composites such as ``"XS / 34 / 6"`` (alpha / EU / UK), and picking one component
requires knowing the garment's category and origin market. That conversion is
valuation-critical, so it waits for Step 1 where it can be validated against real
sold data. Guessing here would silently poison every comp block.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "ALPHA_SIZES",
    "norm_brand",
    "norm_colour",
    "norm_size",
    "norm_text",
    "strip_accents",
]

_WS = re.compile(r"\s+")
_PUNCT_TO_SPACE = re.compile(r"[^\w\s/.-]+", re.UNICODE)

#: Canonical alpha sizes, longest-first so ``XXXL`` wins over ``XXL``.
ALPHA_SIZES: tuple[str, ...] = (
    "XXXS",
    "XXS",
    "XS",
    "S",
    "M",
    "L",
    "XL",
    "XXL",
    "XXXL",
)


def strip_accents(raw: str) -> str:
    """Drop combining marks. ``"Très bon état"`` -> ``"Tres bon etat"``.

    The Vinted reference tables ship FR-locale titles, and UK listings routinely
    contain accented brand names, so accent folding is not optional.
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def norm_text(raw: str) -> str:
    """Casefold, fold accents, drop punctuation, collapse whitespace.

    Guarantees, all covered by property tests:

    * idempotent -- ``norm_text(norm_text(s)) == norm_text(s)``
    * no leading, trailing, or repeated whitespace
    * equal to its own casefold
    """
    folded = strip_accents(raw).casefold()
    despunct = _PUNCT_TO_SPACE.sub(" ", folded)
    return _WS.sub(" ", despunct).strip()


def norm_brand(raw: str) -> str:
    """Normalise a brand name.

    Apostrophes and periods are removed rather than spaced, so ``"Levi's"`` and
    ``"Levis"`` collide, and ``"A.P.C."`` becomes ``"apc"``. This is the one place
    we deliberately lose information: brand strings on Vinted are user-entered and
    the variants are not meaningfully distinct.

    Accents are folded **before** the punctuation is stripped, not after. NFKD
    decomposes typographic punctuation into ASCII -- U+2024 ONE DOT LEADER becomes a
    full stop, U+FF07 FULLWIDTH APOSTROPHE becomes an apostrophe -- so stripping
    first would leave a character that a second pass then removes, making the
    function non-idempotent and splitting one comp block in two. Phone keyboards
    emit these routinely, so this is a live case rather than a theoretical one.
    """
    folded = strip_accents(raw)
    squashed = folded.replace("'", "").replace("\u2019", "").replace(".", "")
    return norm_text(squashed)


def norm_colour(raw: str) -> str:
    """Normalise a colour label. Thin wrapper -- kept separate so that Step 1 can
    add a synonym table without touching call sites."""
    return norm_text(raw)


def norm_size(raw: str) -> str:
    """Normalise a size label to a stable blocking key.

    Recognised alpha sizes are upper-cased and stripped of separators, so
    ``"x-small"``, ``"XSmall"`` and ``"xs"`` all become ``"XS"``. Everything else
    -- numerics, composites, EU shoe sizes, bra sizes -- is returned in normalised
    text form, unconverted. An unrecognised size is a valid size; it just cannot
    be blocked on as tightly.
    """
    text = norm_text(raw)
    if not text:
        return ""

    compact = text.replace(" ", "").replace("-", "").replace("/", "")
    canonical = _ALPHA_SYNONYMS.get(compact)
    if canonical is not None:
        return canonical
    if compact.upper() in ALPHA_SIZES:
        return compact.upper()
    return text


def _build_alpha_synonyms() -> dict[str, str]:
    """Expand each canonical alpha size into its written-out spellings.

    ``XXL`` gains ``xxlarge``, ``extraextralarge``, ``2xl`` and ``2xlarge``. Built
    once at import so the mapping is a plain dict lookup at call time.
    """
    words = {"S": "small", "M": "medium", "L": "large"}
    numeric_prefix_from = 2  # "XXL" also spelled "2XL"; "XL" is never "1XL"
    out: dict[str, str] = {}
    for size in ALPHA_SIZES:
        base = size[-1]
        x_count = len(size) - 1
        prefix_x = "x" * x_count
        prefix_word = "extra" * x_count
        word = words[base]
        variants = {
            size.casefold(),
            f"{prefix_x}{word}",
            f"{prefix_word}{word}",
        }
        if x_count >= numeric_prefix_from:
            variants.add(f"{x_count}x{base.casefold()}")
            variants.add(f"{x_count}x{word}")
        out.update(dict.fromkeys(variants, size))
    return out


_ALPHA_SYNONYMS: dict[str, str] = _build_alpha_synonyms()
