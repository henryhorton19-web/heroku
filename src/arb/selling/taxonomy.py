"""eBay taxonomy compliance: refuse to publish a listing eBay will not index.

Since August 2026, Size and Condition are required on new fashion listings and
non-standard, missing or invalid values are blocked, held, or accepted-but-not-indexed.
That last outcome is the dangerous one. A blocked listing fails loudly and you fix it.
An unindexed listing sits in your inventory looking exactly like a live listing,
costs you the item, and sells nothing. You find out weeks later from the absence of
views.

So this is a **hard gate, not a warning**, and it runs locally before the publish
call rather than relying on eBay to reject: the local answer is immediate and free,
and eBay's answer is sometimes silence.

**The enums are per category and must be fetched, not guessed.** `parse_aspects`
returns `None` for a category that is not in the payload rather than falling back to
another category's enums — validating womenswear against menswear rules would pass a
listing that is then held. Same posture as `value()` refusing below the comp floor:
refusing to answer beats answering wrongly.

**Field names come from `ebay_rest`'s shipped OpenAPI models**
(`commerce_taxonomy.models`, read via `attribute_map`), not from example code. The
raw payload is cached and parsed on read, mirroring `comps_cache`, so a parser fix
never needs a refetch.

Three details that each cost a real bug if missed:

*FREE_TEXT versus SELECTION_ONLY.* Many aspects list enum values *and* accept
anything — Brand is the obvious one. Treating its listed values as a whitelist would
reject every brand eBay has not indexed yet, which is most of the ones worth trading.

*Conditional values.* `aspectValues[].valueConstraints` scopes a value to another
aspect's value: "10-11 Years" is a real Size, but only when Department is Girls.
Flattening every value into one set would accept a childrenswear size on a
womenswear listing — non-standard, held, invisible.

*Condition is not an aspect.* It is a separate listing field and never appears in
the Taxonomy response, so the enum walk cannot enforce it. It is checked explicitly.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from arb.models import ListingDraft

__all__ = [
    "Aspect",
    "AspectMode",
    "CategoryAspects",
    "PublishVerdict",
    "Violation",
    "ViolationKind",
    "parse_aspects",
    "validate_draft",
]

CONDITION_FIELD = "Condition"
"""Not a Taxonomy aspect. Named here so violations read uniformly."""

SIZE_ASPECT = "Size"


class AspectMode(StrEnum):
    FREE_TEXT = "FREE_TEXT"
    SELECTION_ONLY = "SELECTION_ONLY"


class ViolationKind(StrEnum):
    MISSING_REQUIRED = "missing_required"
    NOT_IN_ENUM = "not_in_enum"
    NOT_APPLICABLE = "not_applicable"
    """The value exists in the enum but is scoped to a different value of another
    aspect -- a Girls size on a Women's listing."""
    TOO_LONG = "too_long"


class ValueRule(NamedTuple):
    """One allowed value, with the aspect values it is scoped to (empty = always)."""

    value: str
    scoped_to: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def applies(self, chosen: dict[str, str]) -> bool:
        """True when every scope condition is satisfied by the draft's other aspects.

        An unspecified gating aspect counts as satisfied. Being strict there would
        block a valid draft for not stating something eBay did not require.
        """
        for gate_name, gate_values in self.scoped_to:
            selected = chosen.get(gate_name)
            if selected is not None and selected not in gate_values:
                return False
        return True


class Aspect(NamedTuple):
    name: str
    mode: AspectMode
    required: bool
    values: tuple[ValueRule, ...]
    max_length: int | None = None
    required_by: str | None = None
    """`expectedRequiredByDate`. Not yet enforced, but a deadline that arrives
    unannounced arrives as a wall of held listings."""

    @property
    def allowed_values(self) -> tuple[str, ...]:
        return tuple(rule.value for rule in self.values)


class CategoryAspects(NamedTuple):
    category_id: str
    category_tree_id: str
    aspects: tuple[Aspect, ...]


class Violation(NamedTuple):
    aspect: str
    kind: ViolationKind
    got: str
    allowed: tuple[str, ...] = ()
    """What would have been accepted. A gate that says no without saying what yes
    looks like just moves the problem."""


class PublishVerdict(NamedTuple):
    publishable: bool
    violations: tuple[Violation, ...]
    warnings: tuple[str, ...] = ()

    @property
    def blocking_reason(self) -> str | None:
        """A stable, sorted reason string, or None when publishable."""
        if self.publishable:
            return None
        parts = sorted({f"{v.aspect}:{v.kind.value}" for v in self.violations})
        return "taxonomy:" + ",".join(parts)


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _value_rule(raw: object) -> ValueRule | None:
    if not isinstance(raw, dict):
        return None
    value = _text(raw.get("localizedValue"))
    if value is None:
        return None
    scopes: list[tuple[str, tuple[str, ...]]] = []
    constraints = raw.get("valueConstraints")
    if isinstance(constraints, list):
        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            gate = _text(constraint.get("applicableForLocalizedAspectName"))
            allowed = constraint.get("applicableForLocalizedAspectValues")
            if gate is None or not isinstance(allowed, list):
                continue
            scopes.append((gate, tuple(str(v) for v in allowed)))
    return ValueRule(value=value, scoped_to=tuple(scopes))


def _aspect(raw: object) -> Aspect | None:
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("localizedAspectName"))
    if name is None:
        return None
    constraint = raw.get("aspectConstraint")
    constraint = constraint if isinstance(constraint, dict) else {}
    mode_raw = _text(constraint.get("aspectMode")) or AspectMode.FREE_TEXT.value
    mode = (
        AspectMode.SELECTION_ONLY
        if mode_raw == AspectMode.SELECTION_ONLY.value
        else AspectMode.FREE_TEXT
    )
    max_length = constraint.get("aspectMaxLength")
    raw_values = raw.get("aspectValues")
    rules = (
        tuple(rule for rule in (_value_rule(v) for v in raw_values) if rule is not None)
        if isinstance(raw_values, list)
        else ()
    )
    return Aspect(
        name=name,
        mode=mode,
        required=constraint.get("aspectRequired") is True,
        values=rules,
        max_length=max_length if isinstance(max_length, int) else None,
        required_by=_text(constraint.get("expectedRequiredByDate")),
    )


def parse_aspects(payload: object, *, category_id: str) -> CategoryAspects | None:
    """Parse a `getItemAspectsForCategory` payload. `None` if the category is absent.

    Returning `None` rather than an empty aspect set is the important part: an empty
    set validates everything, so a cache miss would silently disable the gate.
    """
    if not isinstance(payload, dict):
        return None
    entries = payload.get("categoryAspects")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        category = entry.get("category")
        found = category.get("categoryId") if isinstance(category, dict) else None
        if str(found) != category_id:
            continue
        raw_aspects = entry.get("aspects")
        parsed = (
            tuple(a for a in (_aspect(r) for r in raw_aspects) if a is not None)
            if isinstance(raw_aspects, list)
            else ()
        )
        return CategoryAspects(
            category_id=category_id,
            category_tree_id=str(payload.get("categoryTreeId") or ""),
            aspects=parsed,
        )
    return None


def _chosen_aspects(draft: ListingDraft) -> dict[str, str]:
    """Flatten the draft into the aspect map eBay will receive.

    Size and Brand are named fields on the draft rather than free-form specifics
    because they are the two the August 2026 rules turned into hard requirements.
    """
    chosen: dict[str, str] = {name: value for name, value in draft.aspects if value.strip()}
    if draft.size.strip():
        chosen[SIZE_ASPECT] = draft.size.strip()
    if draft.brand.strip():
        chosen["Brand"] = draft.brand.strip()
    return chosen


def _check_enum(aspect: Aspect, value: str, chosen: dict[str, str]) -> Violation | None:
    """Enum membership for a SELECTION_ONLY aspect, honouring value constraints.

    The two failures are separated because they need different fixes. Not in the
    enum means you typed something eBay does not recognise. Not applicable means the
    value is real but scoped elsewhere -- a Girls size on a Women's listing -- and
    the fix is usually the gating aspect, not this one.
    """
    matched = [rule for rule in aspect.values if rule.value == value]
    if not matched:
        return Violation(aspect.name, ViolationKind.NOT_IN_ENUM, value, aspect.allowed_values)
    if not any(rule.applies(chosen) for rule in matched):
        applicable = tuple(r.value for r in aspect.values if r.applies(chosen))
        return Violation(aspect.name, ViolationKind.NOT_APPLICABLE, value, applicable)
    return None


def _check(aspect: Aspect, chosen: dict[str, str]) -> Violation | None:
    """Validate one aspect against the draft. `None` when it passes."""
    value = chosen.get(aspect.name)
    if value is None:
        if aspect.required:
            return Violation(aspect.name, ViolationKind.MISSING_REQUIRED, "", aspect.allowed_values)
        return None
    if aspect.max_length is not None and len(value) > aspect.max_length:
        return Violation(aspect.name, ViolationKind.TOO_LONG, value)
    if aspect.mode is AspectMode.FREE_TEXT:
        return None
    return _check_enum(aspect, value, chosen)


def validate_draft(draft: ListingDraft, aspects: CategoryAspects) -> PublishVerdict:
    """Decide whether eBay will accept *and index* this listing. Pure.

    Reports every violation rather than the first. One round trip per fix is a bad
    loop when publishing a hundred items, and the violations are independent.
    """
    chosen = _chosen_aspects(draft)
    violations = [v for v in (_check(a, chosen) for a in aspects.aspects) if v is not None]

    if draft.condition_band is None:
        violations.append(Violation(CONDITION_FIELD, ViolationKind.MISSING_REQUIRED, "", ()))

    warnings = tuple(
        f"{a.name} becomes required on {a.required_by}"
        for a in aspects.aspects
        if a.required_by and not a.required and a.name not in chosen
    )
    return PublishVerdict(
        publishable=not violations, violations=tuple(violations), warnings=warnings
    )
