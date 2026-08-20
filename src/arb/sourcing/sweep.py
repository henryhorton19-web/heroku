"""The active-listing sweep: measuring how long things actually take to sell.

`days_to_sell` is the denominator of `capital_velocity`, which is the ranking key for
the entire buy side — and eBay's sold endpoint does not carry it. The response has an
end date and no listing-start date, so every completed sale arrives with its duration
already thrown away. **P2** has been open since day one for this reason.

The route that works is watching the *active* side. Browse exposes `itemCreationDate`,
so a listing can be observed while it is still live, watched, and timed when it goes.

Two things make this harder than it looks, and both are handled here rather than
papered over.

**A snapshot of active listings is length-biased, so their ages are not durations.**
The obvious shortcut — pull every active listing, compute `now - itemCreationDate`,
take the median — is wrong, and wrong in a direction that flatters nothing. A slow
listing is live for longer and therefore appears in more snapshots, so any snapshot
over-represents slow sellers. The mean age of currently-active listings is biased
upward relative to the mean time-to-sell. This is the inspection paradox, and it is
why this module tracks *cohorts* instead: observe a listing when it appears, watch
until it disappears, and record the duration that actually elapsed. Listings still
live at the end of the window are **right-censored** — you know they lasted at least
N days, not how long they will last — and they are excluded from the fitted figure
rather than counted as if they sold today.

**A disappearance is not a sale.** A listing leaves search when it sells, when it is
ended unsold, and when the seller delists it. Fashion runs on 30-day cycles and
ended-unsold is ordinary, so treating every disappearance as a sale would understate
time-to-sell badly, and confidently. A disappearance is only counted once it is
corroborated by the same item id turning up in completed sales; everything else is
reported as `unconfirmed` and used for nothing.

That corroboration has a trap in it. Browse returns **two** identifiers: `itemId` in
its RESTful form (`v1|1234|0`) and `legacyItemId` as the bare number. SoldComps
returns the bare number. Matching the RESTful id against sold data corroborates
nothing at all, silently, and the sweep would report every disappearance as
unconfirmed forever while looking like it was working.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from arb.money import parse_pence

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "ActiveListing",
    "Resolution",
    "SweepDiff",
    "diff_actives",
    "parse_active_listings",
    "resolve_disappearances",
]


class ActiveListing(NamedTuple):
    """One live listing, as the sweep needs it."""

    external_id: str
    """The **legacy** numeric id, not the RESTful `v1|...|0` form. Sold data keys on
    the bare number, and corroboration is the entire point of holding this."""

    created_at: datetime
    title_norm: str
    price_pence: int


class SweepDiff(NamedTuple):
    """What changed between two observations of the same search."""

    appeared: tuple[str, ...]
    still_active: tuple[str, ...]
    disappeared: tuple[str, ...]


class Resolution(NamedTuple):
    """Disappearances, split by whether a sale can actually be evidenced."""

    sold: tuple[tuple[str, int], ...]
    """Item id to measured days-to-sell. The only output anything may fit against."""

    unconfirmed: tuple[str, ...]
    """Gone from search with no matching completed sale. Ended unsold, delisted, or
    simply not yet visible in the sold feed. Counted, never fitted."""

    @property
    def confirmation_rate(self) -> float:
        """Share of disappearances that were corroborated sales.

        Worth watching rather than just logging: a rate collapsing toward zero means
        either the id formats have stopped matching or the market has stopped
        clearing, and those need very different responses.
        """
        total = len(self.sold) + len(self.unconfirmed)
        return len(self.sold) / total if total else 0.0


def _created_at(raw: object) -> datetime | None:
    """Parse `itemCreationDate`. eBay emits RFC-3339 with a trailing `Z`.

    `fromisoformat` accepts the `Z` suffix directly from Python 3.11; the usual
    `.replace("Z", "+00:00")` is left over from older versions and ruff flags it.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def parse_active_listings(payload: object) -> tuple[ActiveListing, ...]:
    """Parse a Browse `search` response into sweep observations. Pure.

    Drops entries missing an id, a creation date or a usable price rather than
    substituting defaults: a listing with no creation date cannot be timed, and
    carrying it with a guessed start would put a fabricated duration into the only
    dataset that closes P2.
    """
    if not isinstance(payload, dict):
        return ()
    summaries = payload.get("itemSummaries")
    if not isinstance(summaries, list):
        return ()

    parsed: list[ActiveListing] = []
    for entry in summaries:
        if not isinstance(entry, dict):
            continue
        legacy = entry.get("legacyItemId")
        created = _created_at(entry.get("itemCreationDate"))
        price = entry.get("price")
        if not isinstance(legacy, str) or created is None or not isinstance(price, dict):
            continue
        pence = parse_pence(
            price.get("value") if isinstance(price.get("value"), str) else None,
            currency=price.get("currency") if isinstance(price.get("currency"), str) else None,
        )
        title = entry.get("title")
        if pence is None or not isinstance(title, str):
            continue
        parsed.append(
            ActiveListing(
                external_id=legacy,
                created_at=created,
                title_norm=title.strip().lower(),
                price_pence=pence,
            )
        )
    return tuple(parsed)


def diff_actives(known: Iterable[str], observed: Iterable[str]) -> SweepDiff:
    """Compare a previous observation against a new one. Pure.

    Sorted output so a scheduler's logs and a test's assertions are both stable;
    set iteration order would make either flap for no reason.
    """
    known_set = set(known)
    observed_set = set(observed)
    return SweepDiff(
        appeared=tuple(sorted(observed_set - known_set)),
        still_active=tuple(sorted(observed_set & known_set)),
        disappeared=tuple(sorted(known_set - observed_set)),
    )


def resolve_disappearances(
    disappeared: Sequence[str],
    created_at: Mapping[str, datetime],
    sold_at: Mapping[str, datetime],
) -> Resolution:
    """Split disappearances into corroborated sales and everything else. Pure.

    A duration is produced only when both ends are known: the venue's creation date
    and a completed sale for the same id. Missing either means no measurement, not a
    measurement with a substituted end point.

    Durations are floored at zero and never at one. `rank.capital_velocity` floors
    its divisor at one day, which is where a same-day sale is stopped from looking
    infinitely fast; doing it again here would quietly turn a real zero-day
    observation into a one-day one before it ever reached a fit.
    """
    sold: list[tuple[str, int]] = []
    unconfirmed: list[str] = []
    for external_id in disappeared:
        start = created_at.get(external_id)
        end = sold_at.get(external_id)
        if start is None or end is None:
            unconfirmed.append(external_id)
            continue
        sold.append((external_id, max((end - start).days, 0)))
    return Resolution(sold=tuple(sorted(sold)), unconfirmed=tuple(sorted(unconfirmed)))
