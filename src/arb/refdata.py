"""Load the Vinted ID tables from `0AlphaZero0/Vinted-data` into `vinted_ref`.

Written against the real payloads, not a guessed shape. What the files actually
contain, verified 19 Aug 2026:

===========  ==========================================================
`brand`      flat list, ~2.5k entries, `id` + `title` + `item_count`
`catalog`    4 roots nesting 4 deep, 796 nodes, every node has a `code`
`color`      28 entries with a locale-independent `code` (BLACK, GREY)
`size`       45 groups, 15 non-empty, entries nest under `sizes`
`status`     5 entries, IDs 6/1/2/3/4, no `code` field
`country`    7 entries, continental EU only -- **no GB**
===========  ==========================================================

Two consequences that shape everything downstream:

1. **The titles are French.** This is a `vinted.fr` capture, so `title` is advisory
   and joins must key on `id` or `code`, both of which are locale-independent.
2. **The brand list is a seed, not a census.** Stone Island, Barbour, Patagonia,
   Arc'teryx, Berghaus and Columbia are all absent. Treating membership here as a
   brand allowlist would filter out precisely the stock worth buying, so this table
   is a lookup and never a gate.

Size titles are composites such as ``"XS / 34 / 6"`` (alpha / EU / UK). They are
stored verbatim. Splitting them requires category context and is Step 1 work.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from arb.db import VintedRef
from arb.models import utcnow
from arb.norm import norm_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session

__all__ = ["REF_FILES", "load_reference_data"]

REF_FILES: dict[str, str] = {
    "brand": "brand.json",
    "catalog": "catalog.json",
    "colour": "color.json",
    "size": "size.json",
    "status": "status.json",
    "country": "country.json",
}
"""kind -> filename. `material.json` is deliberately not loaded: it is not used for
clothing resale and loading unused reference data is just latency."""


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Brand(_Row):
    id: int
    title: str
    item_count: int | None = None


class _Catalog(_Row):
    id: int
    title: str
    code: str | None = None
    item_count: int | None = None
    catalogs: list[_Catalog] = []


class _Colour(_Row):
    id: int
    title: str
    code: str | None = None


class _Status(_Row):
    id: int
    title: str


class _SizeEntry(_Row):
    id: int
    title: str


class _SizeGroup(_Row):
    id: int
    description: str | None = None
    sizes: list[_SizeEntry] = []


class _Country(_Row):
    id: int
    title: str
    iso_code: str | None = None


class RefRow(BaseModel):
    """One flattened reference row, ready to upsert."""

    kind: str
    external_id: str
    code: str | None
    title: str
    parent_id: str | None
    item_count: int | None


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_catalog(node: _Catalog, parent: str | None) -> Iterator[RefRow]:
    yield RefRow(
        kind="catalog",
        external_id=str(node.id),
        code=node.code,
        title=node.title,
        parent_id=parent,
        item_count=node.item_count,
    )
    for child in node.catalogs:
        yield from _walk_catalog(child, str(node.id))


def _parse(kind: str, raw: object) -> list[RefRow]:
    """Flatten one reference file into `RefRow`s. Raises on unexpected shape."""
    if kind == "brand":
        return [
            RefRow(
                kind=kind,
                external_id=str(b.id),
                code=None,
                title=b.title,
                parent_id=None,
                item_count=b.item_count,
            )
            for b in TypeAdapter(list[_Brand]).validate_python(raw)
        ]
    if kind == "catalog":
        roots = TypeAdapter(list[_Catalog]).validate_python(raw)
        return [row for root in roots for row in _walk_catalog(root, None)]
    if kind == "colour":
        return [
            RefRow(
                kind=kind,
                external_id=str(c.id),
                code=c.code,
                title=c.title,
                parent_id=None,
                item_count=None,
            )
            for c in TypeAdapter(list[_Colour]).validate_python(raw)
        ]
    if kind == "status":
        return [
            RefRow(
                kind=kind,
                external_id=str(s.id),
                code=None,
                title=s.title,
                parent_id=None,
                item_count=None,
            )
            for s in TypeAdapter(list[_Status]).validate_python(raw)
        ]
    if kind == "country":
        return [
            RefRow(
                kind=kind,
                external_id=str(c.id),
                code=c.iso_code,
                title=c.title,
                parent_id=None,
                item_count=None,
            )
            for c in TypeAdapter(list[_Country]).validate_python(raw)
        ]
    if kind == "size":
        return list(_parse_sizes(raw))
    msg = f"unknown reference kind: {kind}"
    raise ValueError(msg)


def _parse_sizes(raw: object) -> Iterator[RefRow]:
    """Size groups become `size_group` rows; their entries become `size` rows
    parented to the group, so a group's sizing system stays recoverable."""
    for group in TypeAdapter(list[_SizeGroup]).validate_python(raw):
        yield RefRow(
            kind="size_group",
            external_id=str(group.id),
            code=None,
            title=group.description or f"group {group.id}",
            parent_id=None,
            item_count=None,
        )
        for entry in group.sizes:
            yield RefRow(
                kind="size",
                external_id=str(entry.id),
                code=None,
                title=entry.title,
                parent_id=str(group.id),
                item_count=None,
            )


def load_reference_data(session: Session, data_dir: Path) -> dict[str, int]:
    """Upsert every reference file found in `data_dir`. Returns rows written per kind.

    Missing files are skipped rather than fatal: the upstream repo adds and removes
    files, and a partial reference load is more useful than none.
    """
    counts: dict[str, int] = {}
    now = utcnow()
    for kind, filename in REF_FILES.items():
        path = data_dir / filename
        if not path.is_file():
            continue
        rows = _parse(kind, _read(path))
        for row in rows:
            stmt = sqlite_insert(VintedRef).values(
                kind=row.kind,
                external_id=row.external_id,
                code=row.code,
                title=row.title,
                title_norm=norm_text(row.title),
                parent_id=row.parent_id,
                item_count=row.item_count,
                loaded_at=now,
            )
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["kind", "external_id"],
                    set_={
                        "code": stmt.excluded.code,
                        "title": stmt.excluded.title,
                        "title_norm": stmt.excluded.title_norm,
                        "parent_id": stmt.excluded.parent_id,
                        "item_count": stmt.excluded.item_count,
                        "loaded_at": stmt.excluded.loaded_at,
                    },
                )
            )
            counts[row.kind] = counts.get(row.kind, 0) + 1
    session.commit()
    return counts
