"""Quality filtering: exclude listings that will not resell cleanly.

This is a **v0 lexicon and it is expected to be wrong at the edges.** The remainder
comes from completed trades, where the realised outcome shows which phrases actually
predicted a bad buy and which were noise. Measure false negatives against
hand-labelled listings before trusting it.

The asymmetry that shapes every choice here: **a missed opportunity costs nothing, a
false positive costs the trade.** So the filter is aggressive, and when a signal is
ambiguous the listing is rejected rather than passed.

Two failure modes are guarded explicitly because both are easy to get wrong:

*Negation.* "no flaws", "no stains", "not damaged" are the words of a *good* listing.
Matching "flaw" naively rejects exactly the stock worth buying.

*Word boundaries.* Substring matching turns "grease" into a "crease" hit and rejects
half of Vinted. Every term is matched on token boundaries.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import NamedTuple

from arb.norm import norm_text

__all__ = ["LEXICON", "QualityVerdict", "RejectReason", "assess"]


class RejectReason(StrEnum):
    DAMAGE = "damage"
    WEAR = "wear"
    AUTHENTICITY = "authenticity"
    BUNDLE_AMBIGUOUS = "bundle_ambiguous"
    WRONG_AUDIENCE = "wrong_audience"
    INCOMPLETE = "incomplete"


LEXICON: dict[RejectReason, tuple[str, ...]] = {
    RejectReason.DAMAGE: (
        "damaged",
        "damage",
        "broken",
        "faulty",
        "spares or repairs",
        "for parts",
        "ripped",
        "rip",
        "torn",
        "tear",
        "hole",
        "holes",
        "burn",
        "burnt",
        "stained",
        "stain",
        "stains",
        "marked",
        "mark",
        "marks",
        "discoloured",
        "discolored",
        "yellowing",
        "mouldy",
        "moldy",
        "smells",
        "smelly",
        "odour",
    ),
    RejectReason.WEAR: (
        "worn",
        "well worn",
        "bobbling",
        "pilling",
        "faded",
        "fading",
        "threadbare",
        "frayed",
        "fraying",
        "misshapen",
        "stretched",
        "shrunk",
        "shrunken",
    ),
    RejectReason.AUTHENTICITY: (
        "replica",
        "inspired by",
        "unauthorised",
        "unauthorized",
        "unbranded",
        "fake",
        "copy",
        "dupe",
        "reproduction",
    ),
    RejectReason.BUNDLE_AMBIGUOUS: (
        "bundle",
        "job lot",
        "joblot",
        "mixed lot",
        "multi listing",
        "multiple items",
        "pick one",
        "choose one",
        "read description",
        "see description",
    ),
    RejectReason.WRONG_AUDIENCE: (
        "kids",
        "childrens",
        "children",
        "toddler",
        "baby",
        "infant",
        "boys",
        "girls",
        "age 3",
        "age 4",
        "age 5",
        "years",
        "yrs",
    ),
    RejectReason.INCOMPLETE: (
        "missing",
        "no tags",
        "without tags",
        "incomplete",
        "one only",
        "single shoe",
        "empty box",
        "box only",
        "tags removed",
    ),
}
"""Term to reason. Terms are normalised and matched on word boundaries.

`WRONG_AUDIENCE` is here because kids' sizing collides with adult alpha sizes and
poisons the comp block, not because children's clothing is unsellable.
"""

_NEGATORS = ("no", "not", "never", "without", "zero", "free from", "free of")

_NEGATION_WINDOW = 3
"""Tokens before a hit that are scanned for a negator. Three catches 'no visible
stains' and 'not a single mark' without reaching across a sentence boundary."""

_NEGATION_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in _NEGATORS) + r")(?!\w)")
"""Matched as a regex over the window text rather than token-by-token, because
several negators are multi-word ('free from', 'free of'). Comparing single tokens
meant those could never fire, so 'free from holes' was rejected as damage -- caught
by test_negated_condition_language_is_accepted."""


class QualityVerdict(NamedTuple):
    accepted: bool
    reasons: tuple[RejectReason, ...]
    matched_terms: tuple[str, ...]

    @property
    def skip_reason(self) -> str | None:
        """A `decisions.skip_reason` string, or None when accepted."""
        if self.accepted:
            return None
        return "quality:" + ",".join(sorted({r.value for r in self.reasons}))


def _compile() -> list[tuple[re.Pattern[str], RejectReason, str]]:
    compiled: list[tuple[re.Pattern[str], RejectReason, str]] = []
    for reason, terms in LEXICON.items():
        for term in terms:
            normalised = norm_text(term)
            pattern = re.compile(rf"(?<!\w){re.escape(normalised)}(?!\w)")
            compiled.append((pattern, reason, normalised))
    return compiled


_COMPILED = _compile()


def _is_negated(haystack: str, start: int) -> bool:
    """True when a negator sits within `_NEGATION_WINDOW` tokens before the hit.

    'no visible stains' describes a listing worth buying. Rejecting it would filter
    out precisely the sellers who bothered to be explicit about condition.
    """
    window = " ".join(haystack[:start].split()[-_NEGATION_WINDOW:])
    return _NEGATION_RE.search(window) is not None


def assess(title: str, description: str = "") -> QualityVerdict:
    """Judge a listing from its text. Rejects on any un-negated lexicon hit."""
    haystack = norm_text(f"{title} {description}")
    reasons: list[RejectReason] = []
    matched: list[str] = []
    for pattern, reason, term in _COMPILED:
        match = pattern.search(haystack)
        if match is None or _is_negated(haystack, match.start()):
            continue
        reasons.append(reason)
        matched.append(term)
    if not reasons:
        return QualityVerdict(accepted=True, reasons=(), matched_terms=())
    return QualityVerdict(
        accepted=False, reasons=tuple(dict.fromkeys(reasons)), matched_terms=tuple(matched)
    )
