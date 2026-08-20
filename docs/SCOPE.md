# SCOPE — hard constraints

PART D of the build plan, reproduced because `ROADMAP.md` and `AGENTS.md` reference
it. These are operational constraints, not preferences: each one changes whether the
code works or whether it is licensed to run.

---

## eBay apparel size standardisation — enforcing now

Automatic normalisation of high-confidence size values began June 2026 ("Small" →
"S"); custom size entry on new listings was removed during June. From August 2026,
non-standard, missing or invalid size values are **blocked or placed on hold**, and
**Size and Condition are required on new fashion listings**. Non-compliant values are
not indexed by search.

Implementation: pull allowed enums per category from the Taxonomy API
(`getItemAspectsForCategory`), cache them, validate locally before publish, and keep
"Women's" / "Petite" / "Plus" in their own item specifics rather than concatenated
into Size.

Side benefit: normalised eBay sizes make `size_norm` more reliable, which improves
comp matching.

---

## `rembg` model licence

MIT library, but the model weights are licensed independently. The default
`bria-rmbg` requires a paid agreement for commercial use.

**Always pass `-m u2net_cloth_seg` explicitly.** It is clothing-specific and avoids
the licence problem.

---

## Vinted access

Automated access is against Vinted's terms. Contract, not criminal; the realistic
consequence is action against the account used. Personal use means you are the only
exposed party. Keep trading accounts separate from anything you cannot lose.

Request pacing defaults to 1.5 req/s and is capped at 2.0 in `Settings`.

---

## Agent Role Boundaries & Scope Alignment

This repository strictly separates core application architecture (handled by Claude) from advanced automation, stealth, anti-detection, and multi-venue execution tools (handled by DeepSeek/Aider).

### Claude Responsibilities (Core Application Architecture)
* Core business management dashboard logic, analytics, fee calculations, and SQLite accounting models.
* Standard marketplace API integrations (eBay official API, Vinted standard read adapter).
* Clean data normalisation, quality filters, and deterministic CI quality gates.
* Pure domain models, valuation logic (`value()`), and inventory state tracking (`Scouted` -> `Sniped` -> `In-Transit` -> `Enhanced` -> `Listed` -> `Sold`).

### DeepSeek / Aider Responsibilities (High-Throughput Automation & Infrastructure)
* Advanced anti-detection stack (TLS/JA3 spoofing via `curl_cffi` / `tls-client`).
* Proxy fleet management (residential/ISP rotation per task).
* Stealth browser automation (AdsPower, Multilogin, nodriver integration).
* Automated CAPTCHA solving pipeline (CapSolver, 2Captcha APIs).
* Sub-millisecond scraping, AutoCop unattended execution, and automated reposting / multi-channel cross-listing.

