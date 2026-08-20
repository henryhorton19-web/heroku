"""Domain models. Money is integer pence everywhere; floats never touch a price.

These are the wire and in-memory types. `arb.db` holds the persistence tables that
mirror them. Keeping the two separate costs a little duplication and buys the
ability to change storage without changing the valuation contract.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "VINTED_STATUS_TO_BAND",
    "Attributes",
    "CompQuery",
    "ConditionBand",
    "Confidence",
    "Decision",
    "DecisionMode",
    "DecisionOutcome",
    "Listing",
    "ListingDraft",
    "ListingFilter",
    "NonNegPence",
    "Opportunity",
    "Pence",
    "SoldObservation",
    "Valuation",
    "Venue",
    "utcnow",
]

Pence = Annotated[int, Field(strict=True)]
"""Signed integer pence. Realised margin can be negative; a loss is a real number."""

NonNegPence = Annotated[int, Field(strict=True, ge=0)]
"""Unsigned integer pence, for prices and costs, which cannot be below zero."""

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Venue(StrEnum):
    VINTED = "vinted"
    EBAY = "ebay"


class ConditionBand(StrEnum):
    """The five bands Vinted actually uses, in descending quality order.

    Vinted's own labels are locale-dependent (the public reference tables ship
    French titles) but the numeric status IDs are stable, so `VINTED_STATUS_TO_BAND`
    keys on IDs. eBay's condition enums are a separate mapping and are deferred to
    Step 4, where they must be pulled live from the Taxonomy API per category.
    """

    NEW_WITH_TAGS = "new_with_tags"
    NEW_WITHOUT_TAGS = "new_without_tags"
    VERY_GOOD = "very_good"
    GOOD = "good"
    SATISFACTORY = "satisfactory"


VINTED_STATUS_TO_BAND: dict[int, ConditionBand] = {
    6: ConditionBand.NEW_WITH_TAGS,
    1: ConditionBand.NEW_WITHOUT_TAGS,
    2: ConditionBand.VERY_GOOD,
    3: ConditionBand.GOOD,
    4: ConditionBand.SATISFACTORY,
}
"""Vinted `status_id` -> band. IDs verified against 0AlphaZero0/Vinted-data."""


class DecisionMode(StrEnum):
    MANUAL = "manual"
    AUTOBUY = "autobuy"
    DRYRUN = "dryrun"


class DecisionOutcome(StrEnum):
    BOUGHT = "bought"
    SKIPPED = "skipped"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Attributes(_Frozen):
    """Normalised item attributes. Produced by the cached LLM extraction in Step 1.

    `brand_norm` and `size_norm` are the comp blocking key together with
    `condition_band`, which is why they are required while the rest are optional.
    """

    brand_norm: str
    title_norm: str
    size_norm: str
    colour_norm: str | None = None
    condition_band: ConditionBand | None = None
    category_id: str | None = None
    country: str | None = None


class Listing(_Frozen):
    """A live listing seen on a venue at a point in time.

    `favourites`, `views`, `seller_id`, `first_seen` and `last_seen` are forward
    capture per the build plan: nothing in this build reads them, and none of them
    can be reconstructed later.
    """

    venue: Venue
    external_id: str
    url: str | None = None
    price_pence: NonNegPence
    total_pence: NonNegPence | None = None
    attrs: Attributes
    seller_id: str | None = None
    favourites: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    first_seen: datetime
    last_seen: datetime

    @model_validator(mode="after")
    def _seen_order(self) -> Self:
        if self.last_seen < self.first_seen:
            msg = "last_seen precedes first_seen"
            raise ValueError(msg)
        return self


class SoldObservation(_Frozen):
    """One completed sale. The atom of valuation."""

    brand_norm: str
    title_norm: str
    size_norm: str
    colour_norm: str | None = None
    condition_band: ConditionBand | None = None
    category_id: str | None = None
    country: str | None = None
    price_pence: NonNegPence
    ship_pence: NonNegPence | None = None
    listed_at: datetime | None = None
    sold_at: datetime | None = None

    @property
    def days_to_sell(self) -> int | None:
        """Days between listing and sale, or None if either timestamp is missing.

        This is the input to capital velocity and the reason the listed->sold spread
        is captured rather than just the price.
        """
        if self.listed_at is None or self.sold_at is None:
            return None
        return max((self.sold_at - self.listed_at).days, 0)


class CompQuery(_Frozen):
    """A request for comparable sales. Its hash is the append-only cache key.

    The hash must stay stable across releases or the cache silently fragments and
    the accumulating trend series is lost, so it is computed from an explicit sorted
    field list rather than from the model dump.
    """

    brand_norm: str
    title_norm: str
    size_norm: str | None = None
    condition_band: ConditionBand | None = None
    category_id: str | None = None

    @property
    def query_hash(self) -> str:
        payload = {
            "brand_norm": self.brand_norm,
            "category_id": self.category_id,
            "condition_band": self.condition_band.value if self.condition_band else None,
            "size_norm": self.size_norm,
            "title_norm": self.title_norm,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Valuation(_Frozen):
    """What an item sells for and how fast.

    `est_p25` is the fast-sale price, `est_p60` the optimal one. Refusing to return
    a valuation is a valid outcome and is preferred over a confident wrong one, so
    callers construct this only once the comp floor is met.
    """

    est_p25_pence: NonNegPence
    est_p60_pence: NonNegPence
    comp_n: int = Field(ge=0)
    est_confidence: Confidence
    match_confidence: Confidence
    days_to_sell_p50: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _percentile_order(self) -> Self:
        if self.est_p60_pence < self.est_p25_pence:
            msg = "est_p60 below est_p25: percentiles inverted"
            raise ValueError(msg)
        return self


class Opportunity(_Frozen):
    """A scored listing. Ranked on `capital_velocity`, never on `roi`."""

    listing_id: int
    valuation: Valuation
    fees_pence: NonNegPence
    ship_in_pence: NonNegPence
    ship_out_pence: NonNegPence
    net_pence: Pence
    roi: float
    capital_velocity: float | None = None
    qty: int = Field(default=1, ge=1)
    fee_table_version: str = Field(min_length=1)
    scored_at: datetime


class Decision(_Frozen):
    """A buy or skip. Written for every judgement, including ones made by hand.

    `skip_reason` is mandatory on a skip. Without it there is no way to later measure
    whether an automated buyer would have done better, and the dry-run comparison
    flatters the automation by default.
    """

    opportunity_id: int
    mode: DecisionMode
    outcome: DecisionOutcome
    skip_reason: str | None = None
    decided_at: datetime
    spend_pence: NonNegPence | None = None

    @model_validator(mode="after")
    def _reason_required_on_skip(self) -> Self:
        if self.outcome is DecisionOutcome.SKIPPED:
            if not (self.skip_reason and self.skip_reason.strip()):
                msg = "skip_reason is required when outcome is skipped"
                raise ValueError(msg)
            if self.spend_pence:
                msg = "a skipped decision cannot have spend"
                raise ValueError(msg)
        elif self.skip_reason is not None:
            msg = "skip_reason must be empty when outcome is bought"
            raise ValueError(msg)
        return self


class ListingFilter(_Frozen):
    """Input to `BuyVenue.search`. Kept as data so the scanner stays a pure function."""

    query: str | None = None
    brand_norms: tuple[str, ...] = ()
    category_ids: tuple[str, ...] = ()
    size_norms: tuple[str, ...] = ()
    condition_bands: tuple[ConditionBand, ...] = ()
    min_price_pence: NonNegPence | None = None
    max_price_pence: NonNegPence | None = None
    country: str | None = None
    limit: int = Field(default=96, ge=1, le=1000)

    @model_validator(mode="after")
    def _price_order(self) -> Self:
        lo, hi = self.min_price_pence, self.max_price_pence
        if lo is not None and hi is not None and hi < lo:
            msg = "max_price_pence below min_price_pence"
            raise ValueError(msg)
        return self


class ListingDraft(_Frozen):
    """A listing about to be published. Step 4 refuses to publish this locally unless
    `size` is a member of the cached Taxonomy enum for `category_id` and `condition`
    is populated -- eBay blocks non-compliant fashion listings as of August 2026."""

    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1)
    category_id: str = Field(min_length=1)
    price_pence: NonNegPence
    size: str = Field(min_length=1)
    condition_band: ConditionBand
    brand: str = Field(min_length=1)
    image_paths: tuple[str, ...] = ()
    qty: int = Field(default=1, ge=1)


def utcnow() -> datetime:
    """Timezone-aware now. The only sanctioned clock read in the codebase."""
    return datetime.now(UTC)
