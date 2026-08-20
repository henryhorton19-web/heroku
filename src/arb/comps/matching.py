"""Matching a candidate item to comparable sales.

Two stages, deliberately separate. **Blocking** is a hard filter on the fields that
must agree -- brand, and size where both sides state one. **Scoring** is a fuzzy
title comparison that produces `match_confidence`.

Keeping them apart matters because they fail differently. A blocking failure means
the comp is about a different product. A low score means it might be the right
product described differently. Collapsing both into one similarity number loses that
distinction, and it is the distinction that tells you whether a thin comp set means
"rare item" or "bad query".

`match_confidence` is reported separately from `est_confidence` throughout. A tight
comp set that was matched badly and a loose comp set that was matched well are
different problems with different fixes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from arb.norm import norm_brand, norm_size

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arb.models import CompQuery, SoldObservation

__all__ = ["MIN_TITLE_SCORE", "blocks", "match_confidence", "select_comps"]

MIN_TITLE_SCORE = 72.0
"""Titles below this similarity are dropped. Set for precision: an unrelated item in
the comp set moves the median, and the resulting estimate looks perfectly ordinary."""


def blocks(query: CompQuery, obs: SoldObservation) -> bool:
    """Hard gate. Brand must agree; size must agree when both sides state one.

    A missing size on either side is treated as compatible rather than as a
    mismatch, because plenty of legitimate listings omit it -- but a *stated*
    disagreement is fatal, since a medium and an XXL are not the same market.
    """
    if norm_brand(query.brand_norm) != norm_brand(obs.brand_norm):
        return False
    q_size, o_size = query.size_norm, obs.size_norm
    if q_size and o_size and norm_size(q_size) != norm_size(o_size):
        return False
    return not (
        query.condition_band and obs.condition_band and query.condition_band != obs.condition_band
    )


def match_confidence(query: CompQuery, obs: SoldObservation) -> float:
    """Fuzzy title similarity in 0..1.

    `token_set_ratio` is the right shape here: marketplace titles pad the same
    product with varying keyword soup, and token-set comparison is insensitive to
    both order and duplication.
    """
    return fuzz.token_set_ratio(query.title_norm, obs.title_norm) / 100.0


def select_comps(
    query: CompQuery,
    observations: Sequence[SoldObservation],
    *,
    min_title_score: float = MIN_TITLE_SCORE,
) -> tuple[list[SoldObservation], float]:
    """Return the comps that survive blocking and scoring, plus mean confidence.

    Mean confidence is 0.0 for an empty result, so a caller that ignores the empty
    list still gets a number that refuses to look trustworthy.
    """
    threshold = min_title_score / 100.0
    scored = [
        (obs, score)
        for obs in observations
        if blocks(query, obs) and (score := match_confidence(query, obs)) >= threshold
    ]
    if not scored:
        return [], 0.0
    return [obs for obs, _ in scored], sum(s for _, s in scored) / len(scored)
