# ROADMAP — end-to-end build to working beta

**20 August 2026.** Full build-out plan. Nothing here waits on data. Every data gap
runs on a declared placeholder and is swapped for a measurement when one arrives.
Hard constraints are in `docs/SCOPE.md`.

---

## 0. Definition of beta

Beta is reached when all four run end to end, unattended, against live accounts:

```
Dashboard         margins · profit · run rate · verticals · tasks · outstanding
Buyside           scalping · identification · order processing · AutoBuy
Sellside          auto listing · enhance · duplicate to venue · fulfilment
Buyside → Sellside   one valuation engine driving both
```

That last line is an invariant, not a feature. There is one `value()` function: the
call that decides a jumper is worth £42 on the buy side is the same call that prices
your listing and later reprices it. Any change giving the sell side its own pricing
logic breaks the design.

**Current: W1 at 95%.** 354 tests, CI green. The two
remaining W1 tasks each need an external input — credentials, and labelled prices —
so neither is blocked on code.

---

## 1. Placeholder discipline

Build now, measure later. That works as long as a placeholder never gets mistaken for
a measurement, so every one follows the same four rules — already how the fee tables
work, now generalised:

1. **Declared** — carries a flag saying it is a placeholder (`provisional: true`).
2. **Versioned** — content-hashed, so any edit produces a new identity.
3. **Stamped** — the version is written onto every record it influenced
   (`fee_table_version` on `opportunities`).
4. **Listed** — one command shows everything still running on assumptions.

Rules 3 and 4 are what make the whole approach safe. When a placeholder turns out to
be wrong you can find exactly which historical numbers it poisoned and re-score them,
rather than discovering that six weeks of margins were fiction.

### Placeholder register

| # | Gap | Placeholder now | Real source | Blast radius if wrong |
|---|---|---|---|---|
| P1 | eBay/Vinted fees | `provisional: true` YAML | `arb reconcile` ← Sell Fulfillment | every margin and buy decision |
| P2 | `days_to_sell` | `ASSUME_DEFAULT` 30d | active→sold sweep (W4) | ranking order only; `NET` unaffected |
| P3 | Valuation accuracy | unvalidated | `backtest.py`, 100 labelled items | how much to trust `est_confidence` |
| P4 | Quality lexicon | v0 word list | false-negative rate on labelled set | missed buys, and bad buys let through |
| P5 | Postage in/out | config constants | measured per carrier and size band | net margin, roughly £3–4 per trade |
| P6 | Condition discount | none applied | fitted from realised vs band | over-values worn stock |
| P7 | Ledger / dashboard | synthetic seed | real completed sales | nothing — display only |
| P8 | AutoBuy eval set | synthetic decisions | accumulated `decisions` rows | dry-run means nothing until real |
| P9 | Contest thresholds | invented cap + save rate | realised win rate on attempted buys | skipped good stock, or lost races |
| P10 | Repricing decay | assumed 30d optimal→fast | realised days-to-sell vs clearing price | capital sits, or margin given away |

**`arb provenance` — done.** Prints the register against live state: which fee tables
are still provisional, how many realised sales and real decisions exist, which velocity
policy is active, and which `fee_table_version`s scored the existing book. Resolution
closes a placeholder only on positive evidence; "nothing to check" reports `unknown`
rather than passing. See `SPEC.md` §13.

---

## 2. Precedent strategy

Reading working code beats deriving from scratch. Three uses, very different effort:

**Adopt** — install and use: `ebay_rest`, `Pawikoski/vinted-api-wrapper`, `rembg`,
`pdfplumber`, `pypdf`, `rapidfuzz`, `apprise`.

**Harvest** — read for API shape, then implement. Undocumented endpoints have no spec,
so someone else's wrapper *is* the documentation. This is how `sourcing/vinted.py`
was built: the wrapper's dataclasses gave exact field names, so `favourite_count`,
`view_count` and `total_item_price` mapped correctly first time.

**Mine** — take the design, write your own: Vintrack's monitor schema, Stockly's
lifecycle states, `hendt/ebay-api`'s module layout as a map of which endpoints matter.

Two corrections to the source matrix. **The Adyen entries are a category error** —
those repos accept payments as a merchant; AutoBuy drives *Vinted's* checkout, where
Adyen is their processor. They matched on the word "checkout". **`acculister` is
archived and opaque**; the idea worth taking is a marketplace-agnostic listing
payload, and `SellVenue` already is one.

---

## 3. W1 — Buyside

| Task | State | Effort |
|---|---|---|
| Valuation, fees, matching, comps cache, quality, scanner, ranking | done | — |
| `arb scan` / `buylist` / `decide` | done | — |
| Contest-density filter over `favourites` / `views` | done | — |
| `arb provenance` — the register above | done | — |
| Live Vinted session auth | open — needs credentials | 0.5d |
| `backtest.py` — closes **P3** | open — needs 100 labelled items | 1d |

**Running on placeholders:** P2 (velocity), P3 (accuracy), P4 (lexicon), P5 (postage).
None block anything. Run `VelocityPolicy.ASSUME_DEFAULT` and read `NET` and `CONF`
rather than `VEL` until the sweep lands in W4.

**Precedent:** `herissondev/vinted-api-wrapper` as a second reading of the same
endpoints when Pawikoski breaks — and it will, Vinted moves.
`vincenzoAiello/VintedAPI` is archived Node but documents the Android endpoint shapes,
which change less often than the web ones.

---

## 4. W2 — Sellside

Owned item to published listing in under three minutes.

| Task | Effort |
|---|---|
| LLM listing copy + hashtags, structured output | 1d |
| `rembg` sidecar, model `u2net_cloth_seg` passed explicitly | 0.5d |
| **Taxonomy compliance gate** — cache aspect enums, validate before publish | done |
| Publish via `ebay_rest` Sell Inventory | 1.5d |
| Repricing + offer ladders, driven by the same `value()` | done |
| Settlement client — **`sell_finances`**, not Fulfillment; unblocks **P1** | done |
| Labels: `pdfplumber` carrier detect → bbox crop → 6×4 → `pypdf` merge | done |

**The taxonomy gate is a hard blocker, not a nicety.** Since August 2026 Size and
Condition are required on new eBay fashion listings; non-standard values are blocked
or held and are not indexed. A listing that publishes but is not indexed looks like
success and sells nothing.

**Enhance vs duplicate**, since the terms do different work: *enhance* is better
photos, copy and specifics on your own listing (here); *duplicate* is the same item on
a second venue (W5); *re-list to refresh visibility* is out of scope per `docs/SCOPE.md`.

**Precedent:** `hendt/ebay-api` is TypeScript so not adoptable, but its module split
(`buy.browse`, `sell.inventory`, `sell.fulfillment`) maps which endpoints matter and
in what order. `pdf-lib` is the JS analogue of `pypdf` — note the crop-and-merge
approach, keep the Python. Always pass `-m u2net_cloth_seg`; the default `bria-rmbg`
weights need a paid agreement for commercial use.

---

## 5. W3 — Books and dashboard

Builds against synthetic data (**P7**) from day one; real sales replace the seed with
no code change.

| Task | Effort |
|---|---|
| Inventory lifecycle as an explicit column | done |
| Synthetic seed generator — realistic trades for dashboard development | done |
| Ledger: cost basis, real fees, realised net | done |
| Capital deployed vs recycled, ageing over 60 days | done |
| `arb reconcile-fees` — predicted vs realised, rewrites fee YAML, closes **P1** | done |
| Dashboard: margins, profit, run rate, verticals, tasks, outstanding | done |
| HMRC: £1,000 trading allowance flag | done (SA103 box mapping deliberately not) |

**Lifecycle states — adopt Stockly's:** `Scouted → Sniped → In-Transit → Enhanced →
Listed → Sold`. Your `inventory` table implies these through timestamps, but implied
states cannot be queried, counted or aged. As a column it turns "outstanding tasks"
into `WHERE state = 'In-Transit' AND acquired_at < now() - 7 days`.

**Verticals is Niche Finder** — aggregate queries over `category_id`, `country` and
`favourites`, captured since day one for exactly this. No new collection needed.

**Dashboard must render provenance.** Every figure drawn from a placeholder gets
marked as such. A margin computed from provisional fees and a margin computed from
settlement data should not look identical on screen.

**On the stack:** the source matrix proposes Next.js + Prisma + MongoDB — three new
runtimes for a read-only view over SQLite. Start server-rendered from the Python
already here. Adopt Next.js if you want an interactive UI worth its maintenance, and
if so take Stockly's *component layout and analytics views*, not its persistence.

---

## 6. W4 — Automation

| Task | Effort |
|---|---|
| Scheduler + seen-set diff around `scan()` | done |
| Notifications via `apprise` | done |
| Active-listing sweep → real `days_to_sell`, closes **P2** | done (needs 30 durations) |
| AutoBuy rails: spend caps, idempotency keys, dead-man switch | done |
| AutoBuy dry-run harness, runs on synthetic decisions (**P8**) | 1.5d |
| AutoBuy purchase execution | 2d |

**Monitors are cheap because `scan()` is pure** — a scheduler and a seen-set diff wrap
it, nothing inside changes. That purity was bought deliberately in W1.

**AutoBuy writes to the `decisions` table that already exists.** The dry-run harness
can be built and tested against synthetic decisions immediately; it only becomes
*meaningful* once real ones accumulate, which is P8. Build it now, trust it later.

**Order within W4:** rails and dry-run before purchase execution. An AutoBuy without
idempotency keys double-buys on retry, and you find out when two identical jumpers
arrive.

**On latency:** listing-to-feed delay and poll interval dominate; the decision step is
a rounding error. Contest density is the cheaper edge — thin-contest niches at lower
margin beat thick-contest at higher, and it is filter design over data you store.

**Precedent:** Vintrack's monitor config schema (search URL + filters + notification
targets) is a good model; mine the schema, skip the Go workers.
`teddy-vltn/vinted-discord-bot` shows the poll-loop and alert shape. BullMQ is
excellent and premature — revisit when one process genuinely cannot keep up.

---

## 7. W5 — Multi-venue

| Task | Effort |
|---|---|
| Vinted as `SellVenue` | 2d |
| Depop adapter | 3d |
| Poshmark / Mercari adapters | 4d |
| Cross-venue reconciliation and de-listing on sale | 2d |

**De-listing on sale is the part that bites.** Selling the same item twice across two
venues costs a refund, a defect, and sometimes the account. Build reconciliation
before the second adapter, not after.

**Precedent:** `posh-a-matic` and `PoshmarkNursery` show Selenium flows for a
marketplace with no public write API. `SellVenue` already implements `acculister`'s
multi-marketplace abstraction, so new venues are adapters rather than architecture.
Vinted-native sold prices are only reachable if you sell there — a second reason W5
starts with Vinted.

---

## 8. Sequencing

Nothing waits on data. The only real constraints are **code** dependencies.

| Workstream | Effort | Genuinely blocked by |
|---|---|---|
| **W1** Buyside | ~2.5d | — |
| **W2** Sellside | ~8d | `value()` — exists |
| **W3** Books + dashboard | ~7.5d | inventory schema — exists |
| **W4** Automation | ~11d | `scan()` and `decisions` — exist |
| **W5** Multi-venue | ~11d | `SellVenue` protocol — exists |

**All five can start now.** Three couplings are real and worth respecting:

- `arb reconcile` (W3) needs the **Sell Fulfillment client** (W2). Code, not data.
- Cross-venue de-listing (W5) needs **lifecycle states** (W3). Code, not data.
- AutoBuy **purchase execution** waits on rails and dry-run — within W4.

Serial total ~40d. Run in parallel and beta is bounded by the longest track, W4 at
~11d, plus the two couplings.

---

## 9. Hardening — swapping placeholders for measurements

Runs continuously alongside the workstreams; not a phase.

| Trigger | Closes | Action |
|---|---|---|
| First completed sale | P1 | `arb reconcile`, lift `provisional: true` |
| ~30 days of comps cache | P2 | switch to `VelocityPolicy.EXCLUDE`, re-score |
| 100 labelled items | P3 | `arb backtest`; if p25 error > 15%, add a second comps source |
| First 20 realised trades | P4, P6 | retune lexicon; fit the condition discount |
| First 10 shipments | P5 | replace postage constants with measured bands |
| First 50 real decisions | P8 | AutoBuy dry-run becomes meaningful |

**Re-score after every swap.** `fee_table_version` exists so you can find every
opportunity scored under the old assumption and recompute it. A placeholder replaced
without a re-score leaves the old numbers sitting in the database looking correct.

**One ordering rule survives:** do not enable AutoBuy *purchase execution* while P1 is
still open. Everything else can run on placeholders indefinitely — but automated
spending against unmeasured fees repeats a mistake at machine speed, and it is one
`arb reconcile` run away from being fixed.
