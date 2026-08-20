# CONTEXT.md

**Read this before writing any code. Every agent, every session.**

---

## 1. What this is

A personal reselling tool aimed at maximising profit.

---

## 2. Phases, and the hard boundary between them

| Phase | What | Status |
|---|---|---|
| **1** | Working rebuild of Resell Vault's tooling for personal trading profit. Buy side and sell side. | **BUILDING NOW — the only thing in scope** |
| 2 | Repurpose what Phase 1 taught into new marketplaces and asset classes. Moat is multi-marketplace and arbitrage. | Not started. Do not design for it. |
| 3 | Ship as an EU-focused end-to-end marketplace software solution. | Not started. Do not design for it. |

Phases 2 and 3 are recorded above so nobody is surprised by the direction. They are
*not* requirements. If a design decision is justified by "this will help when we go
multi-marketplace" or "we'll need this for EU customers," it is out of scope and
should be removed.

Concretely, the following are **wrong** during Phase 1:

- Generalised abstractions covering marketplaces we don't trade on
- Multi-tenancy, user accounts, auth, billing, entitlements, credit metering
- GDPR controller machinery, DAC7 reporting, VAT OSS
- Entity-resolution or matching infrastructure sized for scale we don't have
- Postgres, message queues, or horizontal-scaling anything

The correct Phase 1 posture is: **the smallest thing that produces a realised profit,
built well.** Phase 2 gets designed when Phase 1 has made money and we know which of
our assumptions were wrong.

Forward compatibility is bought with exactly two things, both cheap: the venue
protocols in `src/arb/protocols.py`, and the forward data capture in §4.4. Nothing else.

---

## 3. Phase 1 scope

### In
- Valuation: what an item sells for, how fast, with a confidence figure
- Buy-side discovery: scan Vinted, rank candidates by return on capital
- Quality filtering: exclude damaged, flawed, mis-sized, ambiguous listings
- Sell-side production: listing copy, photos, publish to Vinted and eBay
- Sell-side operations: repricing, offer ladders, shipping labels
- Books: cost basis, real fees, realised margin, capital deployed, UK tax output

### Out
Monitors and alerting · automated purchasing · niche finder · seller intelligence ·
wholesale and bundle economics · discount monitors · web monitors ·
wardrobe/disappearance tracking · multi-marketplace · arbitrage engine.

---

## 4. Standards

### 4.7 The CI gate is the engineering standard
Most code here is agent-written and will not be line-by-line reviewed. Quality
therefore cannot rest on review — it rests on a deterministic gate that bad code
cannot pass. **Never weaken the gate to make something pass.** No `# type: ignore`,
no dropped lint rules, no relaxed strict mode. If a rule genuinely can't be
satisfied, stop and escalate.

### 4.8 Don't build what you can install
Fee tables, valuation and ranking, the quality lexicon, venue glue, and one LLM
extraction prompt. Everything else — HTTP, retries, ORM, PDF handling, image
segmentation, fuzzy matching, CLI, config — is an installed dependency. If authored
code passes ~4,000 lines, something in the dependency list has been reimplemented.

### 4.10 Precision over recall on anything that spends money
A missed opportunity costs nothing. A false positive costs the trade. Every
threshold — comp count, match confidence, quality filter — is tuned accordingly.
Refuse to produce an estimate rather than produce a confident-looking wrong one.

---

## 5. Repo

**`github.com/henryhorton19-web/heroku`** — public.

Public visibility means Actions minutes are unlimited, and branch protection and
CodeQL code scanning are free. All three are paid-tier features on private repos, so
the gate uses them.

It also means **secrets hygiene is absolute**. Never committed, under any
circumstances:

```
.env              # keys
ebay_rest.json    # eBay keyset + refresh token
arb.db            # trading data
```

All three are gitignored and have `.example` counterparts. `detect-private-key` runs
in pre-commit. Secret scanning is enabled.

### Venue roles
| Venue | Roles |
|---|---|
| Vinted | `BuyVenue` + `SellVenue` |
| eBay | `CompSource` + `SellVenue` |

---

## 6. Dev stack

| Layer | Tool |
|---|---|
| Packaging, envs, locking, running | **uv** |
| Lint + format | **Ruff** |
| Types | **mypy strict** (single source of truth in CI) |
| Tests | **pytest** + **hypothesis** (property tests on all money maths) |
| Mocking | **respx** against recorded fixtures |
| DB | **SQLite** + SQLAlchemy + Alembic |
| CI | **GitHub Actions** + CodeQL |
| Local gate | **pre-commit** |

`uv.lock` is committed. CI runs `uv sync --frozen`.

### Who does what
| Work | Tool | Why |
|---|---|---|
| Verifying facts that go stale — API status, pricing, repo maintenance | **Perplexity** | Free, cited, purpose-built. Marketplace APIs churn. |
| Specs, schema, money maths, test design, diff review | **Claude** | Abundant, and correctness matters most here |
| Multi-file implementation against a written spec | **Antigravity** | Its actual strength. Scarce — rate-limited, no credit pool on free tier |
| The verdict | **GitHub Actions** | Deterministic |

**Antigravity requests are the scarce resource.** Never spend one on exploratory
work. The loop is: spec and failing tests written first, then one agent request per
module, with *"do not modify tests"* in every prompt.
