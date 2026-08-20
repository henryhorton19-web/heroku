# arb — state of development

**Handoff brief for a coding agent.** Read this, then `AGENTS.md`, `ROADMAP.md`,
`SPEC.md` in the repo. Accurate as of 20 August 2026.

---

## 1. What this is

A personal reselling tool. Buy underpriced clothing on Vinted, sell it on eBay, keep
one honest ledger. Python 3.12, SQLite, uv, mypy strict. CLI only — no web service, no
server process. Single-user, local, no auth and no multi-tenancy by design.

**Status: 550 tests, 88% coverage, full CI gate green. 41 modules, 8 migrations, 19
CLI commands.** Nothing has been traded yet, so every figure the tool produces is
still downstream of an assumption — see §5.

---

## 2. The gate — run this before believing anything

```bash
uv sync --frozen
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python scripts/guard.py
uv run pytest
```

CI adds a migrate-from-empty check and `uv lock --check`.

**Never weaken the gate to make something pass.** No `# type: ignore`, `# noqa`,
`# pragma: no cover`, `# nosec` — `scripts/guard.py` fails the build on all four, and
on hand-written `Any` in `src/`. If a rule genuinely cannot be satisfied, stop and
escalate. Two scoped exceptions exist, both documented at the site with the empirical
check behind them (`TC003` on `cli.py`; `ignore_missing_imports` for `vinted.*`).

The authored-line budget that used to be part of this was removed by owner decision;
the underlying rule ("install rather than author") stands in `CONTEXT.md` §4.8.

---

## 3. Invariants — do not break these

| Invariant | Where |
|---|---|
| **One valuation engine.** `comps.valuation.value()` prices buy side, sell side and repricing. `reprice` consumes a `Valuation` and cannot construct one. | `comps/valuation.py`, `selling/reprice.py` |
| **`scan()` is pure.** No HTTP, no DB, no clock read. Monitors, backtests and AutoBuy wrap it. | `sourcing/scanner.py` |
| **`comps_cache` is append-only.** No update, no delete, no dedupe path exists. | `comps/cache.py` |
| **Money is integer pence**, parsed from decimal strings via `Decimal`. Non-GBP is refused, never converted. | `money.py` |
| **`skip_reason` mandatory on a skip**, enforced by construction. | `models.Decision` |
| **`fee_table_version` on every opportunity**, non-null. | `repo.write_opportunity` |
| **Timestamps timezone-aware**; naive rejected on write. | `db.UtcDateTime` |
| **Refusing is a valid output.** `value()` returns `None` below the comp floor. Do not substitute a default. | throughout |
| **Seeding cannot close a placeholder.** Synthetic rows excluded from every provenance count. | `provenance.gather` |

Ranking is on **capital velocity, never ROI**: 40% clearing in five days beats 120%
sitting ninety.

---

## 4. What exists

| Area | Modules | Commands |
|---|---|---|
| Buy side | `comps/` (fees, valuation, matching, cache, service, soldcomps), `sourcing/` (quality, contest, rank, scanner, vinted, sweep), `pipeline`, `repo` | `scan`, `buylist`, `decide` |
| Sell side | `selling/` (taxonomy, aspects_repo, finances, reprice, labels) | `taxonomy load/list/check`, `reprice`, `labels` |
| Books | `books/` (ledger, reconcile, tax, verticals) | `books`, `reconcile-fees`, `tax`, `seed` |
| Automation | `monitor`, `autobuy` | `monitor run/health`, `autobuy arm/stop/resume/status/dryrun` |
| Meta | `provenance`, `dashboard`, `refdata`, `store`, `config` | `provenance`, `dashboard`, `doctor`, `db`, `load-refdata` |

**Installed, not authored:** sqlalchemy, alembic, pydantic, pydantic-settings, typer,
httpx, tenacity, rapidfuzz, pyyaml, vinted-api-wrapper, pdfplumber, pypdf, apprise.

---

## 5. Placeholder register — all 10 open

Nothing has been measured. `arb provenance` prints this against live state; it closes a
placeholder **only on positive evidence**, and "nothing to check" reports `unknown`
rather than passing.

| # | Gap | Standing in | Closed by | Blast radius |
|---|---|---|---|---|
| P1 | eBay/Vinted fees | invented YAML, `provisional: true` | `arb reconcile-fees` ≥10 settled sales | **every margin and buy decision** |
| P2 | `days_to_sell` | assumed 30d | `arb sweep`, ≥30 corroborated durations | ranking order |
| P3 | Valuation accuracy | unvalidated | backtest, 100 labelled items | trust in any estimate |
| P4 | Quality lexicon | v0 word list | 20 realised trades | missed buys, bad buys through |
| P5 | Postage | config constants | 10 measured shipments | ~£3–4/trade net |
| P6 | Condition discount | none applied | 20 realised trades | over-values worn stock |
| P7 | Ledger figures | synthetic seed | 1 settled sale | display only |
| P8 | AutoBuy eval set | none | 50 real decisions | dry-run is meaningless |
| P9 | Contest thresholds | `contest-v0` guesses | realised win rate | skipped good stock / lost races |
| P10 | Repricing decay | `reprice-v0`, 30d linear | realised days-to-sell vs clearing price | capital sits, or margin given away |

**Discipline:** every placeholder must stay *declared* (a flag), *versioned* (content
hash or version string), *stamped* onto what it influenced, and *listed* by
`arb provenance`. Never quietly promote one to a measurement.

---

## 6. Roadmap — remaining work

### Blocked on credentials or environment, not code
| Task | Blocker |
|---|---|
| Publish via `ebay_rest` Sell Inventory (W2) | eBay OAuth keyset. Taxonomy gate already validates drafts pre-publish. |
| LLM listing copy + hashtags (W2) | `ARB_ANTHROPIC_API_KEY` |
| `rembg` background removal (W2) | model weights not fetchable in the build env. **Must pass `-m u2net_cloth_seg`** — default `bria-rmbg` weights need a paid commercial licence. |
| Live Vinted session auth (W1) | credentials |

### Blocked on data accumulating, not code
| Task | Needs |
|---|---|
| `backtest.py` → closes P3 | 100 items with known realised prices |
| Run `arb reconcile-fees --write` → closes P1 | 10 settled sales |
| Run the sweep → closes P2 | 30 corroborated active→sold transitions |
| AutoBuy dry-run becomes meaningful → P8 | 50 real decisions |

### Unblocked, buildable now
| Task | Workstream | Note |
|---|---|---|
| **Cross-venue reconciliation + de-listing on sale** | W5 | **Build this BEFORE the second adapter.** Selling the same item twice costs a refund, a defect, and sometimes the account. |
| Vinted as `SellVenue` | W5 | ~2d. Also the only route to Vinted-native sold prices. |
| Depop / Poshmark / Mercari adapters | W5 | ~3–4d each. `SellVenue` protocol exists; these are adapters, not architecture. |
| AutoBuy purchase execution | W4 | **Hard-blocked while P1 is open** — the rails enforce this, not just the docs. |

---

## 7. Traps — each was a real bug here

| Trap | What happened |
|---|---|
| Best Offer prices | eBay reports the *listed* price on Best Offer sales → `price_is_upper_bound`, excluded by default |
| Quality negation | "no stains" matched "stain" → negation window + multi-word regex |
| Substring matching | "remarkable" matched "mark" → word-boundary matching |
| Comps quota | fetched comps for listings the filter would reject → quality *and* contest pre-filter before fetch |
| Query duplication | sent `nike nike air max 90` → `CompQuery.search_keyword` |
| Non-idempotent norm | U+2024 folds to `.` after punctuation stripping → fold accents *before* stripping |
| Naive datetimes | ambiguous timestamps surface as wrong `days_to_sell` → rejected on write |
| `.gitignore` ate the fee tables | unanchored `data/` also matched `src/arb/data/` → fee tables were never committed, so `fee_table_version` pointed at a file with no history |
| YAML date coercion | unquoted `verified_at: 2026-08-20` parses as `date`, not `str` → emitted fee table could not be read back |
| eBay id formats | Browse returns `v1|1234|0` *and* `legacyItemId`; SoldComps uses the bare number. Matching the wrong one corroborates nothing, silently |
| Length bias | median age of *active* listings ≠ time-to-sell (inspection paradox) → cohort tracking, censored listings excluded structurally |
| Fees live in `sell_finances` | not `sell_fulfillment`; Fulfillment gives only a lump `totalMarketplaceFee`, which cannot correct a componentised table |

**Property tests earn their keep.** Hypothesis found the normalisation bug and an
over-specified fee test. Money maths, blocking keys, spend caps and the repricing band
get property tests, not examples.

---

## 8. How to work here

- **Spec and failing tests first**, then implementation. Update `SPEC.md` every session.
- **Do not modify existing tests** to make new code pass. If a test is genuinely wrong,
  say so explicitly and explain why before changing it.
- **Verify external shapes; do not guess.** `docs/CONTRACTS.md` holds shapes verified
  against live docs or installed package source, with dates. Read `swagger_types` /
  `attribute_map` on `ebay_rest` models rather than inventing field names. Re-verify
  anything older than a month.
- **Tests never hit a live API.** `respx` against recorded fixtures; an autouse fixture
  clears every `ARB_*` variable.
- **Precision over recall on anything that spends money.** A missed opportunity costs
  nothing; a false positive costs the trade. Refuse rather than produce a
  confident-looking wrong number.
- **Secrets.** `.env`, `ebay_rest.json`, `arb.db` are gitignored with `.example`
  counterparts. The repo is public.

**Scope exclusions (`docs/SCOPE.md`), not built under any framing:** reposting to
defeat duplicate detection, discount-code generation, ACO botting, watermark removal,
anti-detection/fingerprint spoofing, proxy rotation to evade blocks, CAPTCHA solving.

**Vinted automated access is against their terms** — contract, not criminal; the
realistic exposure is the account. Keep the trading account separate from anything that
cannot be lost. Rate limit defaults to 1.5 req/s, capped at 2.0.

---

## 9. The one hard ordering rule

**Do not enable AutoBuy purchase execution while P1 is open.** Automated spending
against unmeasured fees repeats a mistake at machine speed, and it is one
`arb reconcile-fees` run away from being fixed. This is enforced in `autobuy.py`, not
merely documented — `arb autobuy status` exits non-zero while P1 is open even when
armed.

Everything else can run on placeholders indefinitely, provided they stay declared.
