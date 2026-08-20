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

## Excluded

Not built, under any framing:

- Reposting to defeat duplicate-listing detection
- Discount-code generation
- ACO botting
- Watermark removal
- Anti-detection and browser-fingerprint spoofing
- Proxy rotation to defeat IP blocking
- CAPTCHA solving

Note the distinction between tool and use: `curl_cffi` is an ordinary HTTP library
and is fine for polite, rate-limited requests. It is impersonation *for the purpose of
evading blocks* that is excluded, not the package.


