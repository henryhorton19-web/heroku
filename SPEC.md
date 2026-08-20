# SPEC — current module contract

**M1 buyside 95%. M2 sellside: all buildable items done. M3 books: complete. M4: sweep, monitors and AutoBuy rails done; execution deliberately blocked.
M5: reconciliation and Vinted SellVenue done.**
Updated 20 Aug 2026. This file is the contract; `CONTEXT.md` is the standing policy.

---

## 1. What exists

| Module | Contract | Lines |
|---|---|---|
| `arb/protocols.py` | `FeeModel`, `CompSource`, `BuyVenue`, `SellVenue`. No I/O, no credentials. | 52 |
| `arb/models.py` | Domain types. Money is `int` pence. All frozen, `extra="forbid"`. | 238 |
| `arb/norm.py` | `norm_text` / `norm_brand` / `norm_size` / `norm_colour`. Blocking keys. | 115 |
| `arb/db.py` | Tables per Part B.4, plus `vinted_ref`. `UtcDateTime` type decorator. | 212 |
| `arb/refdata.py` | Loads the Vinted ID tables into `vinted_ref`. | 212 |
| `arb/money.py` | Decimal-string → integer pence. No float ever touches a price. | 55 |
| `arb/comps/fees.py` | Versioned YAML fee tables, content-hashed into `fee_table_version`. | 110 |
| `arb/comps/valuation.py` | Trimmed p25/p60, confidence, Best Offer exclusion. Returns `None` below the floor. | 75 |
| `arb/comps/matching.py` | Blocking (hard) + rapidfuzz scoring (soft), reported separately. | 45 |
| `arb/comps/cache.py` | Append-only comps cache. No delete path exists, by design. | 40 |
| `arb/comps/soldcomps.py` | SoldComps `CompSource`. Contract verified against their docs. | 145 |
| `arb/sourcing/quality.py` | Quality lexicon v0, negation- and boundary-aware. | 110 |
| `arb/sourcing/contest.py` | Contest density over `favourites`/`views`. Pure. Thresholds provisional. | 157 |
| `arb/selling/taxonomy.py` | eBay aspect enums: parse + validate a draft before publish. Pure. | 270 |
| `arb/selling/aspects_repo.py` | Aspect cache. Upsert, unlike `comps_cache`. | 95 |
| `arb/selling/finances.py` | Settlement parsing from `sell_finances`. Pure. GBP only. | 158 |
| `arb/books/reconcile.py` | Fits fee components to settlement. Refuses below the floor. | 258 |
| `arb/selling/reprice.py` | Ladder + offer bands. Consumes `Valuation`, never produces one. | 200 |
| `arb/selling/labels.py` | Carrier detect, crop to 6x4, merge. Refuses rather than guesses. | 170 |
| `arb/books/ledger.py` | Realised margin, capital deployed, ageing. Settled ≠ estimated. | 190 |
| `arb/books/tax.py` | UK tax year, trading allowance, cash basis. A prep aid, not a filing. | 205 |
| `arb/sourcing/sweep.py` | Cohort tracking for real days-to-sell. Corroborates before counting. | 185 |
| `arb/monitor.py` | Seen-set diff around `scan()` + heartbeat. Staleness is a condition. | 200 |
| `arb/autobuy.py` | Purchase authorisation. Pure, fails closed, never executes. | 215 |
| `arb/books/verticals.py` | Niche aggregates + synthetic seed. Seed rows cannot close a gap. | 160 |
| `arb/dashboard.py` | One self-contained HTML file. Colour encodes provenance. | 245 |
| `arb/selling/crossvenue.py` | Double-sale prevention. Intent before call; hazards from state. | 235 |
| `arb/selling/vinted_sell.py` | Vinted `SellVenue`. Registers before publishing. | 175 |
| `arb/comps/backtest.py` | P3's mechanism. Refuses below 100 labelled items. | 120 |
| `arb/sourcing/rank.py` | Capital-velocity ranking + the unknown-velocity policy. | 120 |
| `arb/sourcing/scanner.py` | `scan()` — **pure function**; monitors and AutoBuy wrap it. | 75 |
| `arb/sourcing/vinted.py` | `BuyVenue` adapter. Pure mapping split from the network call. | 105 |
| `arb/comps/service.py` | Cache-first comp acquisition, quota-aware. | 75 |
| `arb/pipeline.py` | Composes fetch → value → scan. All I/O at the edges. | 70 |
| `arb/repo.py` | Listing upsert, opportunity write, the single decision write path. | 115 |
| `arb/cli.py` | `version`, `db`, `load-refdata`, `doctor`, `scan`, `buylist`, `decide`, `provenance`. | 360 |
| `arb/provenance.py` | The placeholder register, resolved against live state. `gather` I/O, `resolve` pure. | 375 |
| `arb/config.py` | `Settings` via pydantic-settings, `ARB_` prefix. All credentials optional. | 42 |
| `arb/store.py` | Engine, `session_scope`, `upgrade_to_head`. | 40 |

The modules are heavily commented because the reasoning behind a threshold is the
thing that gets lost, not the threshold itself.

Gate status: ruff clean, mypy strict clean, 596 tests, 88% coverage. Full CI gate
verified locally including migrate-from-empty and `uv lock --check`.

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

`capital_velocity` contract, to be confirmed against realised days-to-sell:

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


---

## 8. Facts established from the SoldComps contract (20 Aug 2026)

Verified against sold-comps.com/docs. Each one changed a design decision.

**`days_to_sell` cannot be sourced from eBay comps.** The sold response carries
`endedAt` (a bare date) and **no listing-start field**. Their Poshmark endpoint does
expose `listedAt`/`daysToSell`; the eBay one does not. Since capital velocity is the
ranking key for the entire buy side, its denominator is unavailable on day one. The
three routes are Browse's `itemCreationDate` on active listings, accumulating
active→sold transitions in the append-only cache, or ranking without it.

**`bestOfferAccepted` makes `soldPrice` an upper bound.** eBay never discloses the
accepted offer, so those rows carry the *listed* price. `SoldObservation
.price_is_upper_bound` records it (migration `0002`), and `value()` excludes them by
default. Outlier trimming catches a lone inflated row, but once Best Offer sales are
a third of the set — ordinary in fashion — they are the distribution, not outliers.
Both cases are pinned in tests.

**Three corrections to the build plan.** `count` maxes at **200**, not 240. Prices
arrive as **decimal strings**. `totalItems` is the count on the current page, not a
grand total (`totalResults` is eBay's approximate string, e.g. `"14,000+"`).

**A 429 is two different errors.** `code: rate_limited` is transient; `code:
quota_exceeded` can have a `Retry-After` measured in days. They are separate
exception types so a retry loop cannot conflate them.

---

## 9. Decisions taken during Step 1–2 implementation

**Valuation scores at `est_p25`, not `est_p60`.** Buying against the optimistic
figure is how a plausible margin becomes a loss.

**Unknown velocity excludes by default (`VelocityPolicy.EXCLUDE`).** An item whose
clearing speed is unknown is not ranked. `ScanResult` reports
`suppressed_unknown_velocity` so an empty buy list is distinguishable from a quiet
market. `ASSUME_DEFAULT` exists for the early period but every figure it produces
rests on an unmeasured number.

**Zero-cost listings are counted separately as `suppressed_anomalous_cost`.** A free
item is a data error or a scam, not an infinite-return opportunity, and folding it
into the unpriceable count would make the diagnostics lie.

**Fee tables ship `provisional: true` and a test enforces it.** If that test ever
fails, someone has claimed verification — check they actually did it.

**`SoldObservation.size_norm` and `Attributes.size_norm` are optional.** Comp titles
routinely omit size and both columns were already nullable; the models were stricter
than the schema and than reality. Whether a size-less listing is *buyable* is a
policy question for the quality filter, not a representability question for a model.

---

## 10. What remains

See `ROADMAP.md` for the full plan. Nothing waits on data: every gap runs on a
declared placeholder tracked in the roadmap's placeholder register.

Immediately open in W1, and both need an external input this codebase cannot
supply on its own:

* `backtest.py` — needs 100 items with known realised prices. The harness can be
  written against synthetic labels first, but the number it produces means nothing
  until the labels are real.
* live Vinted session auth — needs credentials and a live handshake to verify.

Done this session: `arb provenance` (§13) and the contest-density filter (§13).

W2 (sellside), W3 (books and dashboard), W4 (automation) and W5 (multi-venue) can all
start in parallel; their code dependencies already exist.

---

## 11. Quota discipline

The free comps tier is 100 requests a month, which is the binding constraint on the
whole buy side. Two mechanisms protect it, both found by running the pipeline
end to end rather than by reasoning about it:

**Quality is assessed before comps are fetched.** The first end-to-end run spent a
request on a listing described as "stained sole" — an item the filter was always
going to reject. `run_scan` now pre-filters, while `scan` remains the authoritative
classifier, so the classification is unchanged and only the spend is avoided.

**Cached payloads are reused within and across runs.** `comps_freshness_days`
(default 7) controls the window. Identical queries inside one scan collapse onto the
first fetch, so a search returning twenty of the same item costs one request.

`arb scan` reports `comps: N cached, M fetched` every run. Fetch-heavy means the
freshness window is too short for how you are searching.

A third correction came out of the same run: `CompQuery.search_keyword` no longer
prefixes the brand when the title already starts with it. Queries were going out as
`nike nike air max 90`, and duplicated tokens dilute eBay's relevance matching —
under `exactMatch=true` they can drop otherwise-good comps entirely.

---

## 12. Running the buy loop

```bash
arb scan "nike air max 90" --max-price 25.00     # fetch, price, rank, persist
arb buylist                                       # ranked, best capital velocity first
arb decide <id> --outcome skipped --reason "..."  # refuses without a reason
arb decide <id> --outcome bought  --spend 12.00   # refuses without a cost basis
```

Both refusals are deliberate. A skip without a reason gives AutoBuy's dry-run nothing
to score against, and a purchase without a cost basis makes every downstream margin
overstated.

Record for every completed sale: predicted net, realised net, actual eBay fees from
the Sell Fulfillment API, actual postage both ways, and days to sell. Those five
numbers are what `arb reconcile` uses to rewrite `data/fees/*.yaml` and lift
`provisional: true`.

---

## 13. Decisions taken building the contest filter and `arb provenance`

### The save rate is the metric, because it is the only age-invariant one

The obvious contest measure is favourites per day, and it is unavailable. Vinted's
search response carries no listing-creation date, and `first_seen` is when *we* saw
the listing, not when it was posted — on a first scan `first_seen == now`, so the
denominator is zero. This is the same missing-denominator shape as `days_to_sell`
(P2), from a different direction.

The save rate sidesteps it entirely:

```
save_rate = favourites / views
```

Both counters accumulate over the same unknown window, so the window cancels. Eight
saves from twenty views is a hot item whether that took two hours or two days. This
is why the roadmap named both fields rather than just `favourites`.

Two rules fire: an absolute favourite cap, and the save rate behind a volume floor.
The floor is load-bearing — without it, one save from one view is a 100% rate and
every brand-new listing is rejected.

### Contest accepts on ambiguity; quality rejects on it

The asymmetry runs the opposite way from `sourcing/quality.py`, deliberately. An
ambiguous *description* usually means a flawed item, so quality rejects. An absent
favourite count means nothing at all about demand, so contest accepts. Rejecting on
missing counters would systematically drop the newest listings — which are the
least contested, and therefore exactly the stock worth buying. Same reasoning as
`vinted_ref` not being a brand allowlist: absence of data is not evidence.

### `ScoreContext` now carries filter policy, not only cost inputs

Adding `contest_policy` as a sixth parameter to `scan()` tripped `PLR0913`. The gate
was right — the signature had outgrown itself — and the fix is the one the codebase
already documents: `ScoreContext` exists so that "adding a component later is a
field, not a signature change at every call site." The policy went there and `scan`
returned to five parameters. The docstring was widened to say honestly that the type
now carries two kinds of thing. **No per-file ignore was added.**

### Contest is pre-filtered in `run_scan`, for the same reason quality is

It reads two integers already on the listing, so applying it before comps are fetched
costs nothing and saves a request against a 100-per-month budget. `scan` re-runs it
and remains the authoritative classifier; the pre-filter changes the spend, never the
verdict. Both halves of that are pinned by tests.

### The register resolves to open by default, and `UNKNOWN` is a distinct state

`resolve()` closes a placeholder only on positive evidence. "Nothing to check"
resolves to `UNKNOWN`, not closed — an empty fee directory technically satisfies "no
table is provisional" and would otherwise read as green. The load-bearing test is
`test_an_empty_system_reports_nothing_closed`: a register that flatters the system
converts a known unknown into an unknown one, which is worse than having no register.

A registered placeholder with no resolver raises rather than being skipped. A gap
that is declared and then silently never checked is the precise failure this module
exists to prevent.

### P9 was added to the register by the same change that created it

The contest thresholds are invented. They ship `provisional=True`, carry a
`contest-v0` version that a retune must bump, and appear in `arb provenance` as P9.
Closed by realised win rate — of the listings you tried to buy, which were gone
before checkout.

They are **not stamped** onto any persisted number, unlike `fee_table_version`, and
the omission is deliberate: a contest verdict influences a boolean rejection, not a
margin, so there is no historical figure for a wrong threshold to poison. Retuning
changes what future scans reject and nothing already written.

### `arb provenance` reports, it does not fail

Every placeholder is open early on, so exiting non-zero would make the command
useless exactly when it is most needed. The one hard ordering rule in ROADMAP §9 —
do not enable AutoBuy purchase execution while P1 is open — is a one-line check
against `resolve()` at the call site that needs it, not a flag here.

It also prints which `fee_table_version`s scored the existing book, and warns when
there is more than one. That is what stamping was for: two versions in one book means
the margins are not comparable with each other and a re-score is owed.

---

## 14. W2 — what the taxonomy gate decided

**The gate is local and runs before publish.** eBay's rejection is sometimes silence:
a non-compliant listing can be accepted and simply not indexed. A local answer is
immediate, free, and — unlike eBay's — always actually arrives.

**A cache miss refuses.** `parse_aspects` returns `None` for an uncached category
rather than an empty aspect set, because an empty set validates everything. A miss
that silently disabled the gate is the one outcome worse than refusing to publish.

**The aspect cache upserts; `comps_cache` appends.** Opposite rules, for a real
reason. A comp is a market observation at a point in time and can never be
re-fetched once the 90-day window rolls past. Taxonomy enums are eBay's *current*
published rules — an old copy is not history, it is a stale rule that will hold your
listing. `category_tree_version` is stored so a bump is visible.

**`ListingDraft.condition_band` became optional.** It was required, which made the
"Condition is missing" case unrepresentable and therefore untestable — the model was
stricter than the failure it needed to describe. The requirement did not disappear;
it moved to the gate, which is where eBay enforces it and where the violation can be
reported alongside the others.

**`ebay_marketplace_id` is `Settings`, not a CLI flag.** Aspect enums differ per
marketplace, so mixing EBAY_GB sizes into an EBAY_US listing is a held listing. It is
an installation property, not a per-invocation choice.

### Still open in W2

| Task | Note |
|---|---|
| Publish via `ebay_rest` Sell Inventory | needs credentials to verify |
| Sell Fulfillment client → unblocks **P1** | highest remaining value |
| Repricing + offer ladders | must call the same `value()`; no second pricing path |
| LLM listing copy | needs `ARB_ANTHROPIC_API_KEY` |
| `rembg` images | pass `-m u2net_cloth_seg`; default weights need a paid licence |
| Labels: `pdfplumber` → crop → `pypdf` | self-contained, testable without credentials |

---

## 15. Reconciliation — what closing P1 decided

**The fees are in `sell_finances`, not `sell_fulfillment`.** A correction to the
roadmap. Fulfillment's `Order` carries `totalMarketplaceFee`, but as a lump sum, and
our table is componentised — any split of one total across three components fits it
equally well. `getTransactions` exposes `orderLineItems[].marketplaceFees[]` with a
`feeType` per fee, which is the level the table is actually written at.

**Reconcile corrects values; it does not infer structure.** The table declares which
components are percentages and which are flat, and reconcile re-measures those
numbers. Inferring the shape from data is possible and a bad idea: with a handful of
settlements almost any structure fits, and the result matches history perfectly while
predicting nothing.

**An unmodelled `feeType` is the important output.** eBay charges `AD_FEE` for
Promoted Listings and the table does not model it. Silently ignoring a fee you are
being charged overstates every margin by exactly that amount, permanently, and
nothing downstream can detect it. Unmapped types are reported to stderr, counted in
the realised total, and carried into the rewritten table as a comment. The end-to-end
run shows why this matters: the assumed final value fee was *too high* (12.50% vs a
measured 12.00%), yet total drift was still **+£1.48 against us**, entirely because
of the fee that was not modelled.

**It refuses below `MIN_SETTLEMENTS`.** A correction fitted to three sales is a guess
that has learned to look like a measurement — worse than the honest guess it would
replace, because it arrives wearing the authority of data. Median, not mean, so one
promoted or discounted order cannot move a rate.

**Refunds are excluded but counted.** Fees are credited back, sometimes on a later
transaction, so a refunded order is not a clean reading of the fee schedule. They do
not count toward the floor, which stops refunds from unlocking a correction.

**Writing bumps `fee_table_version`, and that is the point.** The rewrite changes the
content hash, so every opportunity scored under the old assumption stays findable and
`arb provenance` shows both versions in the book until they are re-scored.

### One bug this work surfaced

`.gitignore` carried an unanchored `data/`, intended for the cloned Vinted reference
data at the repo root. It also matched `src/arb/data/`, so **the fee tables were never
committed**. A fresh clone could not run `arb scan`, and `fee_table_version` — stamped
on every opportunity so that historical assumptions stay recoverable — pointed at a
file with no history. Fixed by anchoring to `/data/`; a regression test asserts on the
pattern. Found by running `--write` end to end and being unable to `git checkout` the
file it had just rewritten.

---

## 16. Repricing — the one-valuation-engine invariant, enforced

`reprice` **consumes** a `Valuation` and cannot construct one. There is no path
through the module that produces a price the valuation engine did not already imply,
which is the invariant made structural rather than merely documented.

The property that encodes it, and the reason it is a Hypothesis test rather than an
example: **an ask never leaves `[est_p25, est_p60]`**, for any valuation, any elapsed
time, any decay window. A price outside that band would be the sell side holding its
own opinion about what an item is worth.

**The two percentiles are used from opposite ends.** Buy side scores at `est_p25` so
a plausible margin cannot quietly become a loss. Sell side *lists* at `est_p60` and
decays toward `est_p25` as the item ages. Buying against the optimistic figure and
selling against the pessimistic one loses money at both ends.

**The floor is `est_p25`, not break-even.** Two different questions, easily confused.
`est_p25` is what the market pays quickly, and the ladder stops there because below
it you are not clearing faster, you are donating. Break-even is what *you* need, and
depends on what you paid — an item bought badly is not worth more because of it. So
break-even is computed and reported, never allowed to move the ladder.

**Break-even is binary-searched over `fee_model.fees_pence`, not solved
algebraically.** With one percentage and one fixed component the algebra is trivial;
the fee table is an arbitrary component list and will grow. The search stays correct
whatever the table becomes and reuses the fee logic instead of restating it.

**Auto-accept is clamped to break-even, not warned about.** It is the only setting
here that cannot be supervised — it fires while you are asleep. When break-even sits
above the ask, `auto_accept_pence` is `None`: there is no offer worth taking
unattended on an item that cannot be sold profitably, and inventing a band would be
worse than refusing one.

**Linear decay, deliberately.** A curve is a claim about how demand decays over time
and nobody has measured that. A straight line is the honest shape for an unmeasured
relationship. The decay window is **P10** in the register.

**One feedback loop worth remembering.** A Best Offer sale reports the *listed* price
to eBay's completed listings, so our own accepted offers re-enter the comp set as
`price_is_upper_bound`. `value()` excludes those by default, which is what stops the
offer ladder from inflating our own future valuations.

---

## 17. Labels and the books

### Labels — refusing beats guessing, again

**A mis-cropped label is a parcel that does not ship.** It fails at the counter,
after the item is packed and the sale is made. So an unrecognised carrier passes
through *uncropped*: that prints badly and visibly, which is the failure you want. A
wrong crop prints beautifully and fails later.

`pdfplumber` reads the text layer, `pypdf` does the geometry. Both installed. The
module is glue and a table of crop boxes.

**The crop boxes are measurements but are deliberately not in the placeholder
register.** Unlike a threshold, a wrong crop box is visible in five seconds by
looking at the output. The feedback loop is immediate and visual, so it needs no
bookkeeping — the register is for assumptions whose wrongness is *silent*.

**Fixtures are generated, not vendored.** Real carrier labels carry live tracking
barcodes and customer addresses, and the repo is public.

### Lifecycle — a column, because implied states cannot be aged

Stockly's states adopted as-is: `scouted → in_transit → listed → sold`. The
timestamps already implied them, but an implied state cannot be queried, counted or
aged, so "what is stuck in transit" was a join over three nullable dates rather than
a WHERE clause.

Migration 0004 backfills from those timestamps, most-advanced rule first. Defaulting
to `scouted` would have reported every historical purchase as unbought.

Deliberately coarse. A richer state machine is easy to write and hard to keep honest:
every state nobody updates becomes a lie that queries then trust.

### The ledger — settled and estimated are never summed

The load-bearing decision. Realised margin uses settlement fees where they exist and
the fee table's prediction otherwise, and `RealisedTrade.settled` carries which all
the way to the report.

They are reported on **separate lines and never added**. Both are plausible numbers;
summed, they produce a total that is neither, and afterwards there is no way to tell
which half was real. Presenting an estimate as a measurement is the specific failure
the whole placeholder discipline exists to prevent, and the books are where it would
be easiest to commit.

**Ageing counts unsold stock only.** Old stock that sold is history; old stock that
has not is the problem, and it is the number that says whether the buy side's
velocity estimates are worth anything.

### Still open

| Task | Blocked by |
|---|---|
| Publish via Sell Inventory | eBay credentials |
| LLM listing copy | `ARB_ANTHROPIC_API_KEY` |
| `rembg` images | model weights are not reachable from this network |
| Dashboard (W3) | nothing — `arb books` is the data behind it |
| HMRC SA103 mapping (W3) | nothing |
| W4 automation, W5 multi-venue | nothing |

---

## 18. Tax output — the module written so it cannot overreach

Everywhere else in this codebase, a confident wrong number costs a trade. Here it
costs a compliance problem, so `books/tax.py` is deliberately built to stop short.

**No SA103 box numbers.** Box numbering changes between tax years and between the
short and full forms. A wrong box number produces a return that is confidently
incorrect. The `sa103_category` column exists for when a mapping has been confirmed
against a specific year's form; nothing in the code fills it in.

**No tax owed.** That needs other income, personal allowance, Scottish rates and a
National Insurance position, none of which live here. It computes turnover, allowable
costs, and the two candidate profit figures — and stops.

**Both methods computed, neither recommended.** Which yields the lower taxable profit
is arithmetic and is reported. Whether to claim it is not, and the asymmetry is real:
deducting actual expenses can create a loss, claiming the allowance cannot.

### Facts verified 20 Aug 2026 for 2026/27

The trading allowance is **£1,000**, unchanged since 2017/18, and applies to **gross
income before expenses** — the part most often got wrong, since £1,500 gross against
£1,400 of costs is over the threshold on £100 of profit. The two methods are mutually
exclusive. Registration deadline is 5 October following the *end* of the tax year.
From 2027/28 a simplified service is expected to change reporting obligations between
£1,000 and £3,000; that changes who must file, not the allowance.

Re-verify against GOV.UK before relying on any of it.

### Cash basis, and why it matters more here than it looks

Cash basis is the default for sole traders: income counts when received, costs when
paid. For a reseller that means **a single trade can straddle two tax years** — bought
in March, sold in May, cost in one year and income in the next. Straddling trades are
counted and reported rather than silently netted, because under traditional accruals
they would be matched instead and the difference is real money in the wrong year.

Unsold stock is still a cost in the year it was paid for, for the same reason.

### Provenance reaches the tax figures too

A sale still costed from the provisional fee table is flagged, and the report says
plainly that these are not tax figures until `arb reconcile-fees` has run. A tax
number resting on an invented fee rate is not a tax number.

### One bug the tests caught

`register_by` was `start_year + 2`. The 2026/27 year ends 5 April 2027, so the
deadline is October 2027 — `+ 1`. Caught against HMRC's own worked example, and
exactly the off-by-one that would have gone unnoticed until it mattered.

---

## 19. The sweep — two ways of not producing a plausible wrong number

`days_to_sell` is the denominator of `capital_velocity`, which ranks the entire buy
side, and eBay's sold endpoint does not carry it. Browse's `itemCreationDate` on the
*active* side is the route, and taking it correctly needs two refusals.

### A snapshot of active listings is length-biased

The shortcut — pull every active listing, compute `now - itemCreationDate`, take the
median — is wrong. A slow listing is live for longer and therefore appears in more
snapshots, so any snapshot over-represents slow sellers and the mean age of actives is
biased upward relative to the mean time-to-sell. The inspection paradox.

So the module tracks **cohorts**: observe on appearance, watch until disappearance,
record the duration that actually elapsed. Listings still live are **right-censored** —
they have lasted at least N days, which is not a duration — and they are excluded
*structurally*: `resolve_disappearances` only ever receives disappearances, so the
exclusion is not a filter anyone can forget.

### A disappearance is not a sale

Listings leave search when they sell, when they end unsold, and when the seller
delists. Fashion runs on 30-day cycles and ended-unsold is ordinary, so counting every
disappearance as a sale would understate time-to-sell badly and confidently. A
duration is produced only when the item id turns up in completed sales; everything
else is `unconfirmed` and fitted to nothing.

`confirmation_rate` is exposed deliberately. A rate collapsing toward zero means
*either* the id formats have stopped matching *or* the market has stopped clearing,
and those need very different responses.

### The trap that would have failed silently

Browse returns two identifiers: `itemId` in RESTful form (`v1|1234|0`) and
`legacyItemId` as the bare number. SoldComps returns the bare number. Matching the
RESTful id corroborates nothing at all — the sweep would report every disappearance as
unconfirmed forever while appearing to work perfectly. Pinned by a test.

### P2's bar was raised, not met

`_resolve_velocity` now needs **30 corroborated durations** before closing P2, not one.
Below that the median is a single slow listing away from moving, and the sweep's whole
value is that its number is trustworthy enough to rank on. Building the mechanism does
not close the placeholder; collecting the data does.

---

## 20. Monitors — silence is ambiguous, so it is made explicit

`scan()` was kept pure in W1 so that monitoring would be additive. It was: a monitor
is a loop, a set difference and a notifier. Nothing inside `scan` changed.

**The failure the design is built around: a monitor that has stopped working looks
exactly like a quiet market.** Both produce no alerts. A crashed scheduler, an expired
session, a changed endpoint and a genuinely empty market are indistinguishable from
outside, and the difference surfaces weeks later as "why have I bought nothing".

Three things follow from that:

*`monitor_runs` is written on the failure path.* A crashed run that leaves no trace is
indistinguishable from one that never started.

*A monitor that has never run reports **stale**, not "healthy, no alerts".* Absence of
alerts is never itself evidence of anything.

*`arb monitor health` exits non-zero when stale*, so a cron wrapper can alert on the
monitor rather than only on what the monitor finds. Something has to watch the watcher.

### Alerts fire on newly-*seen* listings, not every ranked one

The `listings` table is the seen set — `upsert_listing` already preserves `first_seen`
on conflict, and a second seen-set store would be a second thing to keep in sync and
therefore a second thing to drift.

Alerting on everything currently ranked would re-send yesterday's standing inventory
on every poll. Repeated notifications get muted, and a muted monitor is off without
anyone having decided to turn it off. Rejected listings count as seen too, or the next
poll spends comps quota re-pricing stock already rejected.

### One pass, not a loop

`arb monitor run` does a single pass. Scheduling belongs to cron or a systemd timer,
which already handle restarts, overlap and backoff correctly. A hand-rolled loop here
would be a worse version of something already installed on the machine — the same
"install rather than author" rule that put `apprise` in rather than an SMTP client.

With no `ARB_NOTIFY_URL` set, alerts print to stdout. That is the right default rather
than a silent no-op: a monitor whose notifier is misconfigured must not fail silently,
because that is the same silence as a quiet market.

### Still open in W4

AutoBuy: rails (spend caps, idempotency keys, dead-man switch), then the dry-run
harness, then purchase execution — in that order, and **execution stays blocked while
P1 is open**. Automated spending against unmeasured fees repeats a mistake at machine
speed.

---

## 21. AutoBuy rails — authorisation, never execution

`autobuy.py` decides what may be bought and buys nothing. The separation is the
design: the decision to spend is a pure function, exhaustively testable and reviewable
in one file, while the part that would touch a checkout is elsewhere and inert until
this says yes.

**Every rail fails closed.** A missing fact, an expired token, an unreadable state row
— all refuse. A wrongly-refused purchase costs a missed item, which costs nothing. A
wrongly-allowed one costs money at machine speed while nobody is watching.

### The hard rule is enforced, not documented

ROADMAP §9's single ordering rule — do not enable purchase execution while **P1** is
open — is a code path here. `_fees_measured()` consults the same register
`arb provenance` prints rather than restating the condition, because a second
definition of "are the fees real yet" would drift from the first, and this is the one
rail whose being wrong spends money automatically.

Verified live: armed for four hours, `arb autobuy status` still exits 1.

### Armed, not enabled

`armed_until` is an **expiry**, not a flag, so walking away from the machine stops
AutoBuy. A boolean stays true forever, which is exactly the state you do not want to
discover a fortnight later. Capped at 24 hours — the expiry only protects you if it is
shorter than your attention span.

`stop` and `resume` are separate from `arm`: clearing a deliberate halt and re-enabling
spending are two decisions, and collapsing them means a `resume` quietly starts buying.

### Three caps, because they bound three different disasters

Per run bounds a bad batch. Per day bounds a bad afternoon. Outstanding bounds how much
capital can sit in unsold stock at once. **A per-run cap alone permits twenty runs an
hour.** Defaults are deliberately small; the right way to raise one is deliberately,
after the dry-run has been checked, not by discovering the default was already
generous.

A cap breach refuses that item and *continues*, so a cheap good item behind an
expensive one stays reachable. Stopping at the first breach would silently reorder a
buy list that was carefully ranked.

A Hypothesis property covers the whole surface: no ordering of candidates and no prior
spend can authorise more than the caps allow.

### Idempotency

The key is **derived** from venue and listing id, not random, so a retry recomputes the
same key and is refused. A random key per attempt makes every retry look like a new
purchase — the exact failure this prevents. Enforced by a UNIQUE index *and* checked
within a batch, because a batch should not rely on an `IntegrityError` to notice its
own duplicate. Attempt rows are written *before* the purchase, so a crash mid-purchase
leaves a claimed key rather than nothing: the retry is blocked and a human looks.

### What is deliberately not built

**Purchase execution.** It stays unbuilt while P1 is open, and the rails would refuse
it anyway. The dry-run harness exists as a command but is honest that it means nothing
until real decisions accumulate — that is **P8**, and `arb provenance` tracks it.

---

## 22. The dashboard — provenance as the design brief

**One self-contained HTML file. No server, no build step, no second runtime.** The
source matrix proposed Next.js, Prisma and MongoDB for a read-only view over a local
SQLite file — three runtimes to maintain for a page one person reads. `arb dashboard`
writes a file you open. If an interactive UI ever earns its maintenance, the queries
underneath it stay put.

**The brief is provenance, so it is the design.** ROADMAP §5 requires that a margin
computed from provisional fees and one computed from settlement data not look
identical on screen. Colour therefore carries exactly one meaning and no decoration:
**teal is measured, amber is assumed.** Every figure is marked where it is read, not
disclaimed in a footnote.

The register is a full section rather than an appendix. For a tool whose entire
philosophy is refusing to present an estimate as a measurement, burying its own list
of open assumptions would be a lie told by layout.

Figures are monospace throughout — money in a ledger wants tabular alignment. Three
properties are asserted rather than assumed: the page contains no `http(s)` and no
`<script>`, and marketplace-supplied category ids are escaped.

### Seeding cannot close a placeholder

Every generated row is `synthetic=True`, and `provenance.gather` excludes synthetic
rows from every count it takes. **Verified live: 40 seeded trades, P7 still open, 10
of 10 open.** That property is what makes it safe to build a dashboard against
generated data instead of waiting for the first real sale — the alternative is a
dashboard nobody can develop, or one whose demo data quietly becomes its production
data. A test also checks the exclusion is a *filter* and not a short circuit: a real
settled sale alongside seed data still closes P7.

The guard caught a `# noqa: S311` on a seeded RNG here. The fix was better than the
suppression would have been: what this wants is **reproducibility, not randomness**, so
values are derived from a hash of the row index. That also removes sequence state —
seeding 20 rows and seeding 40 agree about the first 20, which a seeded RNG would not.

### Verticals is the niche finder, and needed no new collection

`category_id`, `country` and `favourites` have been on every listing since the first
scan for exactly this. The table reads **margin against watchers**, because margin
alone is half a picture: a high-margin niche with forty watchers per listing is one
you lose races in.

---

## 23. Cross-venue reconciliation — built before the thing it catches

Listing one item on two venues is a few hours of adapter work. Selling it twice costs a
refund, a defect, and sometimes the account. So this landed **before** the second sell
adapter, which is the roadmap's explicit ordering and the reason W5 starts here rather
than with Depop.

**De-listing is a distributed operation and it will partially fail.** The sale lands on
eBay, the Vinted pull times out, and a sold item is still buyable. Nothing about that is
exotic; it is the ordinary behaviour of two systems failing independently. Three things
follow:

*Intent is recorded before the venue call.* `delist_requested_at` when we decide,
`delisted_at` only when a venue confirms. A crash between them leaves findable work.
Call-then-record loses the intent entirely, and the hazard becomes **invisible** rather
than pending — strictly worse than doing nothing, because the queue looks clean.

*A failed de-list keeps its error and stays queued.* Clearing it would make "actively
resisting" look identical to "nobody has tried".

*`hazards()` is a query over state, not an event replay.* It is therefore correct after
a crash, a missed webhook, or a stretch when nothing was running — and those gaps are
precisely when a double sale happens. Event-driven reconciliation inherits every gap in
event delivery.

Four hazard kinds, because each needs a different response: `LIVE_AFTER_SALE` (nothing
in flight, nothing will fix it), `DELIST_FAILED`, `DELIST_PENDING` (benign for minutes,
a hazard for hours — the caller decides from `requested_at`), and `SOLD_TWICE`, which is
unpreventable from here and reported so it is not learned from a buyer's message.

### Vinted as a `SellVenue`

**Registration is the first step of publishing, not the last.** A listing live on a
venue but absent from `own_listings` is invisible to the hazard check — exactly the item
most likely to be sold twice. Registering first means the worst case is a row for a
listing that failed to publish, which is harmless and self-correcting.

Per-venue limits belong in the venue adapter: `ListingDraft` caps titles at 80 for
eBay's limit, Vinted's is 60, so a draft perfectly valid on one venue is still truncated
for the other.

**Deliberately not built: re-listing to refresh visibility.** Vinted's feed rewards
recency and the temptation is obvious. `docs/SCOPE.md` excludes reposting to defeat
duplicate-listing detection, and a convenient framing does not un-exclude it.

### Backtest — P3's mechanism

Refuses below 100 labelled items. A 15% error measured on eleven is not a result; it is
a number carrying the authority of a measurement. Error is against `est_p25`, the figure
the buy side actually scores on — backtesting the number you do not trade against would
be measuring the wrong thing accurately.

Signed error sits beside absolute error because they answer different questions: 12% out
in both directions is noise, 12% high every time is bias, and bias has a fix. A test
pins `MIN_LABELLED_ITEMS` and `MAX_MEDIAN_ERROR` so tuning them to pass shows up in a
diff.
