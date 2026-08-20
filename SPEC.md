# SPEC — current module contract

**M1 buyside 95%. M2 sellside: all buildable items done. M3 books: lifecycle,
ledger, tax and reconcile done. M4: sweep done. M5 not started.**
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

Gate status: ruff clean, mypy strict clean, 487 tests, 87% coverage. Full CI gate
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
