# SPEC — current module contract

**Step 0 complete. Steps 1–4 not started.**
Updated 19 Aug 2026. This file is the contract; `CONTEXT.md` is the standing policy.

---

## 1. What exists

| Module | Contract | Lines |
|---|---|---|
| `arb/protocols.py` | `FeeModel`, `CompSource`, `BuyVenue`, `SellVenue`. No I/O, no credentials. | 52 |
| `arb/models.py` | Domain types. Money is `int` pence. All frozen, `extra="forbid"`. | 238 |
| `arb/norm.py` | `norm_text` / `norm_brand` / `norm_size` / `norm_colour`. Blocking keys. | 115 |
| `arb/db.py` | Tables per Part B.4, plus `vinted_ref`. `UtcDateTime` type decorator. | 212 |
| `arb/refdata.py` | Loads the Vinted ID tables into `vinted_ref`. | 212 |
| `arb/cli.py` | `version`, `db upgrade`, `db current`, `load-refdata`, `doctor`. | 96 |
| `arb/config.py` | `Settings` via pydantic-settings, `ARB_` prefix. All credentials optional. | 42 |
| `arb/store.py` | Engine, `session_scope`, `upgrade_to_head`. | 40 |

**Authored source: 1,009 / 4,000 lines**, enforced by `scripts/guard.py`. The count
includes docstrings, which are a large share of it — the modules are heavily commented
because the reasoning behind a threshold is the thing that gets lost, not the threshold.

Gate status: ruff clean, mypy strict clean, 108 tests, 98% coverage.

---

## 2. Seams (Part B.2), and where they are

| Seam | Where | Status |
|---|---|---|
| Venue protocols | `protocols.py` | Done. Four roles, split so eBay can be `CompSource` + `SellVenue`. |
| Scanner as pure function | — | Step 2. `ListingFilter` is already a frozen data object so `scan(filter, now)` needs no I/O of its own. |
| Decision table | `db.Decisions` + `models.Decision` | Done. `skip_reason` enforced at the model layer. |
| Quantity in the cost model | `FeeModel.fees_pence(price, qty=1)`, `Opportunities.qty`, `Inventory.qty` | Done. |

---

## 3. Deviations from the build plan, and why

Each of these is a deliberate choice, not drift. Reverse any of them freely, but know what it costs.

**`CompSource` is a separate protocol, not a `SellVenue` method.**
Part B.2 lists `SellVenue.sold_comps`, but the venue-roles table in `CONTEXT.md` names `CompSource` as its own role. SoldComps and Apify are comp sources that are not sell venues at all, so folding the two together would mean the comps-overflow provider could not be swapped without touching publishing.

**`vinted_ref` table added beyond the Part B.4 DDL.**
Step 0 requires loading the Vinted ID tables and B.4 gives them no home. Single table keyed `(kind, external_id)` rather than five tables, because the access pattern is identical for all of them and five tables is five migrations later.

**Indexes are plain ASC, not `DESC` as written in the DDL.**
`idx_rank` is declared `(capital_velocity, scored_at)` rather than `(capital_velocity DESC, scored_at DESC)`. SQLite scans an index backwards when every `ORDER BY` term is reversed together, which is exactly the ranking query, so the two are equivalent in plan terms. Plain indexes also avoid false-positive drift in the Alembic autogenerate comparison that guards §5 below. If a query ever mixes directions, this needs revisiting.

**Timestamps are `TEXT` in storage but `datetime` in Python.**
The DDL says `TEXT`, which keeps SQLite's lexical ordering usable. `UtcDateTime` converts at the boundary and **rejects naive datetimes on write**. Ambiguous timestamps would surface months later as a wrong `days_to_sell`, which is an input to capital velocity.

**`mypy`'s `disallow_any_explicit` is off; `scripts/guard.py` enforces the intent instead.**
The setting fires on every `class X(BaseModel)` because pydantic synthesises `__init__(**data: Any)`. It therefore flags the dependency rather than our code, and silencing it would need an override on nearly every module. The guard greps authored source for hand-written `Any` directly, which is what was actually wanted. This is documented in `pyproject.toml` at the site.

**One scoped ruff exception: `"src/arb/cli.py" = ["TC003"]`.**
Typer resolves annotations at runtime via `inspect.get_annotations`, so moving `Path` into a `TYPE_CHECKING` block raises `NameError` at import. Verified empirically before adding the exception, not assumed.

---

## 4. Facts established from real data

These came from inspecting the actual payloads before writing against them. They are recorded here because each one changes a design decision.

**The reference data is a `vinted.fr` capture.** Titles are French; `country.json` holds 7 continental EU countries and **no GB**. Consequences: join on `id` or `code`, never on `title`. All 796 catalog nodes carry a locale-independent `code`.

**The brand table is a seed, not a census.** 2,535 entries, missing Stone Island, Barbour, Patagonia, Arc'teryx, Berghaus and Columbia — squarely the brands worth trading. **`vinted_ref` is a normalisation lookup and never a brand allowlist.** Filtering candidate stock on membership would drop the best stock silently. Guarded by `test_brand_seed_is_not_an_allowlist`.

**Vinted status IDs are `{1, 2, 3, 4, 6}`.** Pinned in `models.VINTED_STATUS_TO_BAND`, keyed on ID because the titles are locale-dependent. If upstream adds a sixth status, `test_vinted_status_map_covers_every_band` fails rather than mapping it to `None` downstream.

**Size labels are composites: `"XS / 34 / 6"` is alpha / EU / UK.** There are no separate UK size groups; the UK value is the third component. Shoe groups use EU numbering. `norm_size` therefore **does not convert between sizing systems** — it canonicalises alpha sizes and returns everything else unconverted. Splitting the composite needs category and origin-market context and belongs in Step 1 where it can be validated against sold data.

---

## 5. Invariants the gate enforces

- **No suppressions.** `# type: ignore`, `# noqa`, `# pragma: no cover`, `# nosec` all fail `scripts/guard.py`. Zero present.
- **No hand-written `Any`** in `src/`. Narrow with `object` and `isinstance`.
- **Authored-line budget of 4,000.** Hitting it means a dependency has been reimplemented.
- **`.gitignore` covers `.env`, `ebay_rest.json`, `arb.db`.** Plus `detect-private-key` in pre-commit and GitHub secret scanning — three layers, because the repo is public.
- **Models and migrations cannot drift.** `test_models_and_migrations_do_not_drift` runs Alembic's `compare_metadata` against the migrated database.
- **Migrations apply from empty** in CI, so a migration that only works against an existing database fails.
- **Tests never reach a live API.** An autouse fixture clears every `ARB_*` variable and chdirs to a temp directory, so no test can pick up a real `.env`.

---

## 6. Step 1 handoff — write these before implementing

Step 1 is the valuation engine. Per `CONTEXT.md`, the loop is spec and failing tests first, then one agent request per module, with *"do not modify tests"* in the prompt.

### Contracts to implement

```python
# arb/comps/fees.py
def load_fee_table(path: Path) -> FeeModel   # YAML, content-hashed into .version

# arb/comps/valuation.py
def value(observations: Sequence[SoldObservation], min_comp_n: int) -> Valuation | None
```

`value` returns `None` below `min_comp_n`. **Refusing is the correct output** — a missed opportunity costs nothing, a false positive costs the trade.

### Definitions to pin before code

`capital_velocity` is deliberately **not** yet implemented, so Step 1 and Step 2 cannot disagree about it. Proposed contract, to be confirmed against real numbers in Step 3:

```
capital_velocity = net_pence / cost_pence / max(days_to_sell_p50, 1)
```

Ranking is on this, never on `roi`: 40% clearing in five days beats 120% sitting ninety.

`est_confidence` should fall out of `comp_n` and IQR/median. `match_confidence` comes from `rapidfuzz` scoring against the blocking key. Keep them separate — a tight comp set badly matched and a loose comp set well matched are different failures.

### Dependencies Step 1 adds

`ebay-rest`, `rapidfuzz`, `duckdb`, `pyyaml`, plus the Claude API call for attribute extraction. Not installed yet — the scaffold declares only what it uses.

### Day 6 gate (Part F)

```
100 items with known realised prices -> median absolute % error of p25 < 15%
no estimate returned with comp_n < 3
every opportunity carries fee_table_version
```

Fail means add Apify as a second comps source. **Do not tune thresholds to pass.**

---

## 7. Known open items

- **`u2net_cloth_seg` must be passed explicitly** to `rembg` (Step 4). The default `bria-rmbg` weights need a paid agreement for commercial use.
- **eBay apparel size standardisation is live this month.** Size and Condition are required on new fashion listings; non-standard values are blocked or held. `ListingDraft` carries both fields, but the Taxonomy enum validation is Step 4 and is a hard gate on publish.
- **Re-verify at build time.** SoldComps' free-tier limit, the eBay size rules, and the `ebay_rest` package's maintenance status all go stale. That verification is Perplexity's job, not Claude's.
- **Vinted automated access is against their terms.** Contract, not criminal; the realistic exposure is the account. Keep the trading account separate from anything that cannot be lost. Rate limit defaults to 1.5 req/s and is capped at 2.0 in `Settings`.
