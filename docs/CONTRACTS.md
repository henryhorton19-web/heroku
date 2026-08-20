# CONTRACTS — verified external API shapes

Every shape below was verified against live documentation or installed package source
on the date given. **Re-verify anything older than a month.** Marketplace APIs move,
and a stale contract here is worse than no contract because it looks authoritative.

Method matters: these were read from primary sources — vendor docs, OpenAPI models
shipped inside packages, dataclasses in wrappers — not inferred from example code.

---

## 1. SoldComps — eBay sold comps

*Verified 20 Aug 2026 against sold-comps.com/docs.*

```
GET https://api.sold-comps.com/v1/scrape
Authorization: Bearer sc_...
```

| Param | Notes |
|---|---|
| `keyword` | required; supports eBay minus-syntax (`-case -lot`) |
| `ebaySite` | `ebay.co.uk` for UK |
| `count` | **max 200** — the build plan said 240, the API says 200 |
| `page` | increment while `hasNextPage` |
| `sortOrder` | `endedRecently` default |
| `exactMatch` | default true; strips eBay's loosened "fewer words" results |
| `soldAfter` / `soldBefore` | post-scrape filters, not server-side |

**Limits:** free tier 100 requests/month, 60/min, ~90 days of history.

### Response

```jsonc
{
  "keyword": "...", "page": 1,
  "totalItems": 200,          // count on THIS page, not a grand total
  "totalResults": "14,000+",  // eBay's approximate string, may be null
  "hasNextPage": true,
  "items": [{
    "itemId": "...", "url": "...", "title": "...",
    "condition": "Pre-Owned", "conditionId": 3000, "categoryId": "...",
    "endedAt": "2026-08-10",      // DATE ONLY — eBay exposes no time of day
    "soldPrice": "44.99",         // DECIMAL STRING, never a number
    "soldCurrency": "GBP",
    "shippingPrice": "3.95",      // "0.00" = free, null = unknown
    "bestOfferAccepted": false,
    "buyingFormat": "buyItNow", "bidCount": null,
    "sellerUsername": "...", "sellerFeedbackScore": 5120,
    "itemLocation": null,         // null = domestic
    "scrapedAt": "2026-08-14T21:00:00.000Z"
  }]
}
```

### Two load-bearing facts

**There is no `listedAt`.** `days_to_sell` cannot be derived from an eBay comp. Their
Poshmark endpoint *does* return `listedAt`/`daysToSell`; the eBay one does not. This
is why `capital_velocity` has no denominator on day one.

**`bestOfferAccepted: true` means `soldPrice` is an upper bound.** eBay never
discloses the accepted offer, so the field carries the *listed* price. Fashion uses
Best Offer heavily. Ingest as `price_is_upper_bound=True`; `value()` excludes them by
default. Outlier trimming catches a lone inflated row but not a third of the set.

### Errors

| Status | `code` | Action |
|---|---|---|
| 429 | `rate_limited` | back off `Retry-After` seconds, retry |
| 429 | `quota_exceeded` | **do not retry** — reset can be days away |
| 502 | — | upstream blocked, transient, retry |
| 503 | — | concurrency limit, retry shortly |

Implemented as separate exception types in `comps/soldcomps.py` so a retry loop
cannot conflate them.

---

## 2. Vinted — read side

*Verified 20 Aug 2026 against `vinted-api-wrapper` installed source.*

`Vinted.search(...)` accepts `query`, `page`, `per_page` (default 96), `price_from`,
`price_to` (**major units**, not pence), `order`, `catalog_ids`, `size_ids`,
`brand_ids`, `status_ids`, `color_ids`, `country_ids`.

`Item` dataclass fields that matter:

| Field | Notes |
|---|---|
| `id`, `title`, `url` | |
| `price` | `Price \| str`; `Price.amount` is a decimal string |
| `total_item_price` | **what you actually pay** — includes buyer protection |
| `brand_title`, `size_title` | strings, may be null |
| `status` | **localised string** ("Very good"), not the numeric id |
| `user.id` | seller — forward capture |
| `favourite_count`, `view_count` | contest density — forward capture |

**Use `total_item_price`, not `price`.** The headline excludes buyer protection;
scoring on it overstates every margin by roughly the fee.

**Condition arrives as a label, not an id.** Reference tables key on ints (`{1,2,3,4,6}`)
but the search response returns English text. `sourcing/vinted.UK_CONDITION_LABELS`
maps it; an unrecognised label becomes `None` rather than a guess.

### Reference tables — `0AlphaZero0/Vinted-data`

*Verified 20 Aug 2026 by cloning and inspecting.*

`DATA/{brand,catalog,color,size,status,country}.json`.

- **FR-locale capture.** Titles are French; `country.json` holds 7 continental EU
  countries and **no GB**. Join on `id` or `code`, never `title`.
- **2,535 brands — a seed, not a census.** Stone Island, Barbour, Patagonia,
  Arc'teryx, Berghaus and Columbia are all absent. Safe as a normalisation lookup,
  **unsafe as a brand allowlist** — filtering on membership drops the best stock.
  Guarded by `test_brand_seed_is_not_an_allowlist`.
- **796 catalog nodes**, 4 roots nesting 4 deep, every node carries a `code`.
- **Size titles are composites**: `"XS / 34 / 6"` is alpha / EU / UK. No separate UK
  groups. Shoe groups use EU numbering. Stored verbatim; `norm_size` does not convert
  between systems.
- **Status ids are `{6,1,2,3,4}`** → new-with-tags, new-without-tags, very-good, good,
  satisfactory.

---

## 3. eBay — via `ebay_rest`

*Verified 20 Aug 2026 against installed package source.*

The package ships OpenAPI-generated models — 75 for `buy_browse` alone. **Read
`swagger_types` on the model rather than guessing field names.**

`ItemSummary` carries 44 fields including `item_id`, `title`, `price`
(`ConvertedAmount`), `condition`, `condition_id`, `item_web_url`, `seller`,
`item_location`, `categories`, `buying_options`, `shipping_options`, and
**`item_creation_date`** — which is the route to a survivorship estimate of
time-to-sell, since sold comps lack it.

Bundled API groups: `buy_browse`, `buy_deal`, `buy_feed`, `buy_marketing`,
`buy_offer`, `buy_order`, `commerce_catalog`, `commerce_charity`, `commerce_identity`,
`commerce_media`, `commerce_message`, and the `sell_*` families.

### Apparel size standardisation — enforcing now

Since August 2026, **Size and Condition are required on new fashion listings**.
Non-standard, missing or invalid size values are blocked or placed on hold, and
non-compliant values are not indexed by search.

Implementation: pull allowed enums per category from Taxonomy
`getItemAspectsForCategory`, cache them, **validate locally before publish**, and keep
"Women's" / "Petite" / "Plus" in their own item specifics rather than concatenated
into Size.

A listing that publishes but is not indexed looks like success and sells nothing.
Treat this as a hard gate, not a warning.

### Taxonomy `getItemAspectsForCategory`

*Verified 20 Aug 2026 against `ebay_rest.api.commerce_taxonomy.models` `attribute_map`.*

```jsonc
{
  "categoryTreeId": "3",
  "categoryTreeVersion": "128",
  "categoryAspects": [{
    "category": { "categoryId": "53159", "categoryName": "..." },
    "aspects": [{
      "localizedAspectName": "Size",
      "aspectConstraint": {
        "aspectRequired": true,
        "aspectMode": "SELECTION_ONLY",   // or FREE_TEXT
        "aspectUsage": "RECOMMENDED",
        "aspectDataType": "STRING",
        "itemToAspectCardinality": "SINGLE",
        "aspectMaxLength": 65,
        "expectedRequiredByDate": "2026-11-01"  // becomes required later
      },
      "aspectValues": [{
        "localizedValue": "10-11 Years",
        "valueConstraints": [{
          "applicableForLocalizedAspectName": "Department",
          "applicableForLocalizedAspectValues": ["Girls"]
        }]
      }]
    }]
  }]
}
```

**Three load-bearing facts, each implemented in `selling/taxonomy.py`:**

*`FREE_TEXT` aspects list enum values but accept anything.* Brand is the common case.
Treating its `aspectValues` as a whitelist rejects every brand eBay has not indexed —
which is most of the ones worth trading.

*`valueConstraints` scopes a value to another aspect's value.* "10-11 Years" is a
real Size but only when Department is Girls. Flattening every value into one set
accepts a childrenswear size on a womenswear listing: non-standard, held, invisible.

*Condition is not an aspect.* It is a separate listing field and never appears in this
response, so the enum walk cannot enforce it. Checked explicitly.

**`expectedRequiredByDate` is a scheduled break.** Surfaced as a warning rather than a
block, because blocking today would be wrong and silence means the deadline arrives as
a wall of held listings.

---

## 4. Anthropic API — attribute extraction and listing copy

Used for one cached extraction call per item (brand / type / size / colour /
condition band) and for listing copy. Cache aggressively: the call blocks the comp
query, and uncached it is the largest per-item cost in the pipeline.

Request structured JSON explicitly and parse defensively — strip fences before
`json.loads`.

---

## 5. Re-verification checklist

| Contract | Verified | Check by |
|---|---|---|
| SoldComps endpoint, limits, fields | 20 Aug 2026 | fetch `sold-comps.com/docs`, or `api.sold-comps.com/openapi.json` |
| Vinted wrapper shape | 20 Aug 2026 | read installed `vinted/models/items.py` |
| Vinted reference tables | 20 Aug 2026 | re-clone `0AlphaZero0/Vinted-data` |
| eBay Browse models | 20 Aug 2026 | `ItemSummary.swagger_types` |
| eBay size rules | 20 Aug 2026 | eBay seller centre announcements |
| Taxonomy aspect shape | 20 Aug 2026 | `Aspect.attribute_map` in `ebay_rest` |

SoldComps was showing a sold-listings outage on 20 Aug 2026 (active listings
unaffected). Check `sold-comps.com/status` before diagnosing an empty comp set as a
code fault.
