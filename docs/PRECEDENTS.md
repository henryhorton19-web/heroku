# PRECEDENTS — open-source sources worth harvesting

Condensed from the precedent research, with a quality warning and the entries that
actually earn their place.

**Read the source matrix critically.** Two entries reveal how it was assembled: the
Adyen repositories are for *accepting* payments as a merchant, which teaches nothing
about driving Vinted's checkout — they matched on the word "checkout". And
`acculister` is described in the document itself as archived and opaque.

---

## Three uses, very different effort

**Adopt** — install and use. Already in the build: `ebay_rest`,
`Pawikoski/vinted-api-wrapper`, `rembg`, `pdfplumber`, `pypdf`, `rapidfuzz`. Planned:
`apprise`.

**Harvest** — read for API shape, then implement against it. Undocumented endpoints
have no spec, so someone else's wrapper *is* the documentation. `sourcing/vinted.py`
was built this way: the wrapper's dataclasses gave exact field names, so
`favourite_count`, `view_count` and `total_item_price` mapped correctly first time
rather than after a week of live debugging.

**Mine** — take the design idea, write your own code.

---

## Buyside

| Source | Use | What to take |
|---|---|---|
| `Pawikoski/vinted-api-wrapper` | adopt | in use; search params, item dataclasses |
| `herissondev/vinted-api-wrapper` | harvest | second reading of the same endpoints when the first breaks — and it will |
| `vincenzoAiello/VintedAPI` | harvest | archived Node, but documents Android endpoint shapes, which move less than web |
| `JakobAIOdev/Vintrack` | mine | monitor config schema: search URL + filters + notification targets. Skip the Go workers |
| `teddy-vltn/vinted-discord-bot` | mine | poll-loop shape and alert structure |

## Sellside

| Source | Use | What to take |
|---|---|---|
| `ebay_rest` (matecsaj) | adopt | in use; ships OpenAPI models — read `swagger_types` |
| `hendt/ebay-api` | mine | TypeScript, not adoptable, but its module split (`buy.browse`, `sell.inventory`, `sell.fulfillment`) maps which endpoints matter and in what order |
| `danielgatis/rembg` | adopt | HTTP sidecar mode (`rembg s`); pass `-m u2net_cloth_seg` |
| `jsvine/pdfplumber` | adopt | carrier detection by text signature, bbox geometry |
| `Hopding/pdf-lib` | mine | JS analogue of `pypdf`; note the crop-and-merge approach |
| `posh-a-matic`, `PoshmarkNursery` | mine | Selenium flows for a marketplace with no public write API |
| `acculister` | mine | archived; the idea is a marketplace-agnostic listing payload, which `SellVenue` already is |

## Dashboard and inventory

| Source | Use | What to take |
|---|---|---|
| Stockly (`arnobt78/Stock-Inventory-Management-System`) | mine | **the lifecycle state machine** — `Scouted → Sniped → In-Transit → Enhanced → Listed → Sold` — and the analytics component layout. Not the Prisma/MongoDB persistence |

## Queueing

`BullMQ` is excellent and premature. Revisit only when a single process genuinely
cannot keep up; that is a real threshold and a long way from 50 items a month.

---

## Not used

The anti-detect block — CAPTCHA solvers, TLS/JA3 impersonation for evasion,
anti-detect browsers, proxy rotation to defeat blocking — is excluded per
`docs/SCOPE.md`.
