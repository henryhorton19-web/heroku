# AGENTS.md — read this first

Operating brief for a coding agent building this to beta. Read this, then
`ROADMAP.md`, then `SPEC.md`. Everything else is reference.

---

## 1. What this is

A personal reselling tool. Buy underpriced clothing on Vinted, sell it on eBay, and
keep one honest ledger. Python 3.12, SQLite, uv, mypy strict.

**Working today** (354 tests, CI green):
valuation · fee model · comp matching · append-only comps cache · SoldComps adapter ·
quality filter · contest-density filter · pure scanner · capital-velocity ranking ·
Vinted read adapter · placeholder register · eBay taxonomy compliance gate ·
`arb scan` / `buylist` / `decide` / `provenance` / `taxonomy` / `reconcile-fees` / `reprice` / `books` / `labels` / `tax` / `monitor` / `autobuy` / `dashboard` / `seed`.

**To build:** sellside publishing, books, dashboard, automation, multi-venue. See
`ROADMAP.md` §3–§7. All five workstreams are unblocked and can run in parallel.

---

## 2. The gate

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python scripts/guard.py
uv run pytest
```

All five must pass. CI runs the same plus a migrate-from-empty check and
`uv lock --check`.

**Never weaken the gate to make something pass.** No `# type: ignore`, no `# noqa`,
no `# pragma: no cover`, no relaxed strict mode, no lowered coverage floor.
`scripts/guard.py` fails the build on any of them. If a rule genuinely cannot be
satisfied, stop and escalate rather than suppress.

Two scoped exceptions exist, both documented at the site with the empirical check
behind them: `TC003` on `cli.py` (Typer resolves annotations at runtime) and
`ignore_missing_imports` for `vinted.*` (the wrapper ships no `py.typed`). Do not add
a third without the same standard of justification.

---

## 3. Invariants — do not break these

**One valuation engine.** `comps.valuation.value()` prices the buy side, the sell
side, and repricing. If the sell side grows its own pricing logic, the design is
broken.

**`sourcing.scanner.scan()` is pure.** No HTTP, no database, no clock read — `now`
arrives in `ScoreContext`. Monitors, AutoBuy dry-runs and backtests all wrap it.
If a feature needs I/O inside `scan`, the feature belongs in the caller.

**`comps_cache` is append-only.** No update, no delete, no dedupe. SoldComps exposes
~90 days; this table is the only way you ever hold more, and it cannot be rebuilt.
There is deliberately no delete path in `comps/cache.py`.

**Money is integer pence.** Never a float. Prices arrive as decimal strings and go
through `arb.money.parse_pence` via `Decimal`. Non-GBP is refused, never converted.

**`skip_reason` is mandatory on a skip.** Enforced by construction in
`models.Decision`, so `record_decision` cannot be handed an invalid one. AutoBuy's
dry-run scores itself against these rows.

**`fee_table_version` on every opportunity.** Non-null. It is how you find which
historical scores a wrong assumption poisoned.

**Timestamps are timezone-aware.** `UtcDateTime` rejects naive datetimes on write.

**Refusing is a valid output.** `value()` returns `None` below the comp floor. Do not
substitute a default. A missed opportunity costs nothing; a confident wrong number
costs the trade.

---

## 4. Pitfalls — each of these was a real bug here

### Who does what
| Work | Tool | Why |
|---|---|---|
| Core Specs, Schema, Valuation Logic, Fee Models, Clean CI Gate | **Claude** | Focuses strictly on core application architecture, data integrity, and compliance. |
| Deep Feature Dev, Anti-Detection, Proxy Fleet, AutoCop, Multi-Venue Execution | **DeepSeek / Aider** | Drives overall execution, stealth infrastructure, TLS spoofing, and high-frequency automation. |
| Verifying facts that go stale — API status, pricing, repo maintenance | **Perplexity** | Free, cited, purpose-built. Marketplace APIs churn. |
| Multi-file implementation against a written spec | **Antigravity** | Its actual strength. Scarce — rate-limited, no credit pool on free tier |
| The verdict | **GitHub Actions** | Deterministic |

| Trap | What happened | Guard |
|---|---|---|
| Best Offer prices | eBay reports the *listed* price on Best Offer sales, so comps skew high | `price_is_upper_bound`, excluded by default |
| Quality negation | "no stains" matched "stain"; "free from holes" was rejected as damaged | negation window + multi-word regex |
| Substring matching | "remarkable" matched "mark", "grease" matched "ease" | word-boundary matching, tested |
| Comps quota | fetched comps for listings the filter would reject anyway | quality pre-filter before fetch |
| Query duplication | sent `nike nike air max 90`; duplicated tokens dilute matching | `CompQuery.search_keyword` |
| Non-idempotent norm | U+2024 (ONE DOT LEADER) NFKD-folds to `.` after punctuation stripping | fold accents *before* stripping |
| Model vs schema drift | `size_norm` was required in the model, nullable in the table | models match the schema |
| Naive datetimes | ambiguous timestamps surface months later as wrong days-to-sell | rejected on write |

**Property tests earn their keep.** Hypothesis found the normalisation bug and an
over-specified fee test of mine. Money maths and blocking keys get property tests,
not examples.

---

## 5. Placeholders — build on them, do not mistake them for data

Ten declared gaps, listed in `ROADMAP.md` §1 with blast radius and printed against
live state by **`arb provenance`**. Run it before trusting any number this tool
produces. The important ones:

- **Fees are invented.** Every table ships `provisional: true`. `arb reconcile`
  replaces them from Sell Fulfillment settlement data. Until then every margin is
  downstream of numbers nobody has checked.
- **`days_to_sell` does not exist in eBay sold comps.** No listing-start date in the
  response. `capital_velocity` has no denominator until the active-listing sweep.
  Run `VelocityPolicy.ASSUME_DEFAULT`; read `NET` and `CONF`, not `VEL`.
- **The quality lexicon is v0** and is expected to be wrong at the edges.

- **Contest thresholds are invented.** The favourite cap and save rate in
  `sourcing/contest.py` ship `provisional=True` under version `contest-v0`. Closed by
  realised win rate: which attempted buys were gone before checkout.

A placeholder must stay declared, versioned, stamped onto what it influenced, and
listed by `arb provenance`. Never quietly promote one to a measurement. `resolve()`
closes one only on positive evidence — "nothing to check" reports `unknown`, never
green.

---

## 6. How to work here

**Spec and failing tests first, then implementation.** Update `SPEC.md` with the
module contract, write the tests, then write the code. Do not modify existing tests
to make new code pass — if a test is genuinely wrong, say so explicitly and explain
why before changing it.

**Verify external shapes; do not guess.** `docs/CONTRACTS.md` holds shapes verified
against live documentation, with dates. Where a package ships models
(`ebay_rest` ships 75 OpenAPI models for Browse alone), read them rather than
inventing field names. Re-verify anything older than a month — marketplace APIs move.

**Tests never hit a live API.** `respx` against recorded fixtures in
`tests/fixtures/`. An autouse fixture clears every `ARB_*` variable so no test can
pick up real credentials.

**Install rather than author.** HTTP, retries, ORM, PDF handling, fuzzy matching,
CLI and config are all installed. Before writing a module, check whether a maintained
package already does it — reimplementing a dependency is the failure mode here.

**Secrets.** `.env`, `ebay_rest.json` and `arb.db` are gitignored and have `.example`
counterparts. The repo is public. `detect-private-key` runs in pre-commit and the
guard checks `.gitignore` coverage.

---

## 7. Layout

```
src/arb/
  protocols.py      BuyVenue · SellVenue · CompSource · FeeModel
  models.py         domain types; money is int pence
  money.py          decimal-string → pence, Decimal only
  norm.py           normalisation; the comp blocking key
  db.py             tables + UtcDateTime
  store.py          engine, session, migrations
  config.py         pydantic-settings, ARB_ prefix
  repo.py           listing upsert, opportunity write, decision write path
  provenance.py     the placeholder register, resolved against live state
  monitor.py        seen-set diff around scan() · heartbeat · staleness
  autobuy.py        purchase authorisation · caps · idempotency · dead-man switch
  pipeline.py       composes fetch → value → scan; all I/O at the edges
  refdata.py        Vinted ID table loader
  cli.py            typer
  comps/            fees · valuation · matching · cache · service · soldcomps
  sourcing/         quality · contest · rank · scanner · vinted · sweep
  selling/          taxonomy gate · aspect cache · settlement parsing · repricing · labels
  books/            ledger, capital and ageing · reconciliation · UK tax · verticals
  dashboard.py      self-contained HTML; colour encodes measured vs assumed
  data/fees/        versioned YAML fee tables
  migrations/       alembic
tests/fixtures/     recorded payloads
scripts/guard.py    the part of the gate lint cannot express
docs/               CONTRACTS · SCOPE · PRECEDENTS
```

---

## 8. Reading order

1. `AGENTS.md` — this file
2. `ROADMAP.md` — what to build, in what order, on what placeholders
3. `SPEC.md` — current module contracts and the decisions behind them
4. `docs/CONTRACTS.md` — verified external API shapes
5. `docs/SCOPE.md` — hard constraints
6. `docs/PRECEDENTS.md` — open-source sources to harvest, with a quality warning
7. `CONTEXT.md` — standing engineering policy
