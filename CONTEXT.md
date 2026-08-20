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
# FEATURE ROADMAP: VINTED ARBITRAGE & RESELLING ENGINE

## 1. Business Management Dashboard (The Hub)
* **Real-Time Analytics:** Live tracking of profit margins, net profit, monthly run-rate, and revenue breakdown.
* **Task & Queue Management:** Visual kanban/list view of pending worker tasks (photo enhancement, listings, shipments).
* **Inventory State Machine:** Track items through `Scouted` -> `Sniped` -> `In-Transit` -> `Enhanced` -> `Listed` -> `Sold` -> `Funds Cleared`.
* **Advanced Fee Calculator:** Pre-compute net margins deducting buyer protection, shipping, and marketplace fees.
* **Shipping Label Processor:** Auto-detect carriers, crop, and merge PDF labels for bulk printing.
* **Automated Accounting:** Expense logging, recurring costs, SKU generation, and tax-ready exports.
* **Goal Tracking:** Progress bars against £2K+ monthly revenue/profit targets.

## 2. Buyside Engine (Sourcing & Sniping)
* **Sub-Millisecond Monitoring:** Zero-delay polling to detect new Vinted listings (target: <0.2ms latency).
* **Custom Filtering:** Real-time scanning for specific brands, sizes, price caps, and keywords.
* **Deal Identification & Profit Filter:** Algorithmic pricing comparison against historical median sold data for true ROI.
* **AutoBuy (One-Tap):** Discord/Webhook push notifications for manual one-click checkout on underpriced items.
* **AutoCop (Unattended Checkout):** 24/7 headless worker to reserve items, select shipping, and process payment autonomously.
* **Wholesale & Drop Alerts:** Secondary monitors for retail price errors, clearance drops, and sneaker releases.

## 3. Sellside Engine (Asset Enhancement & Cross-Listing)
* **Event-Driven Handoff:** Auto-route acquired Buyside inventory data directly to the Sellside processing queue.
* **AI Photo Enhancer:** Background removal, EXIF scrubbing, and application of premium studio/daylight overlays.
* **AI Listing Generator:** LLM-powered generation of SEO titles, condition descriptions, and platform-specific hashtags.
* **Price Estimator:** AI-driven sell price recommendations based on live comps and confidence ranges.
* **Multi-Channel Cross-Lister:** API/headless scripts to publish enhanced listings to eBay, Depop, Poshmark, and Mercari.
* **Automated Reposting:** Extract, crop, and re-upload stale listings to manipulate algorithms and boost views.
* **Inventory Sync (Multi-Channel Lock):** Auto-delist an item from all secondary platforms the moment it sells on one.

## 4. Anti-Detection & Infrastructure
* **TLS & JA3 Spoofing:** `curl_cffi` / `tls-client` integration to bypass Cloudflare and DataDome WAFs.
* **Proxy Fleet Management:** Auto-rotation of residential and sticky ISP proxies assigned per checkout task.
* **Stealth Browser Profiles:** AdsPower/Multilogin integration to isolate synthetic buyer accounts from main seller accounts.
* **CAPTCHA Solving Pipeline:** Automated routing of hCaptcha/Turnstile challenges to CapSolver or 2Captcha APIs.
* **Financial Guardrails:** Hard-coded daily spend caps, idempotency keys (prevent duplicate buys), and a master kill-switch.

---

## 4. Standards

### 4.7 The CI gate is the engineering standard
Most code here is agent-written and will not be line-by-line reviewed. Quality
therefore cannot rest on review — it rests on a deterministic gate that bad code
cannot pass. **Never weaken the gate to make something pass.** No `# type: ignore`,
no dropped lint rules, no relaxed strict mode. If a rule genuinely can't be
satisfied, stop and escalate.

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
