# ENGINE-BOUWPLAN — Hoogfrequente Reselling & Arbitrage Engine

## 1. Architectuur & Event Bus Stroom

```
┌─────────────────────────────────────────────────────────────┐
│  Ingestie (Polling Workers)                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Vinted       │  │ eBay Browse  │  │ Andere       │   │
│  │ Monitor      │  │ Monitor      │  │ Bronnen      │   │
│  └──────┬───────┘  └──────┬──────┘  └──────┬──────┘   │
│         │                  │                  │            │
│         ▼                  ▼                  ▼            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Event Bus (asynchrone queue via asyncio.Queue)    │   │
│  │  - ListingScannedEvent                             │   │
│  │  - PurchaseAuthorisedEvent                         │   │
│  │  - SaleCompletedEvent                              │   │
│  │  - CrossListRequestEvent                           │   │
│  └─────────────────────────────────────────────────────┘   │
│         │                                                  │
│         ▼                                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Verwerkingspijplijn                               │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │   │
│  │  │ Quality  │→│ Valuation│→│ Ranking       │   │   │
│  │  │ Filter   │  │ (value())│  │ (capital_vel)│   │   │
│  │  └──────────┘  └──────────┘  └──────┬───────┘   │   │
│  │                                       │            │   │
│  │                                       ▼            │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  AutoCop / AutoBuy (autobuy.py rails)       │   │   │
│  │  │  - Idempotency keys                         │   │   │
│  │  │  - Spend caps                              │   │   │
│  │  │  - P1 fee check                           │   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  │                                       │            │   │
│  │                                       ▼            │   │
│  │  ┌──────────────────────────────────────────────┐   │   │
│  │  │  Checkout Engine (curl_cffi / headless)      │   │   │
│  │  │  - TLS/JA3 impersonatie                    │   │   │
│  │  │  - CAPTCHA-oplossing                      │   │   │
│  │  │  - Betaling via Adyen-achtige flow        │   │   │
│  │  └──────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Cross-Lister (Sellside)                          │   │
│  │  - eBay REST (hendt/ebay-api patroon)            │   │
│  │  - Depop / Poshmark / Mercari (Selenium)         │   │
│  │  - Auto-delist bij 'Sold'                        │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Event Bus:** Gebruik `asyncio.Queue` voor interne coördinatie.  
**Polling Workers:** Draaien in aparte asyncio-taken, elk met eigen proxy-rotatie en TLS-profiel.

---

## 2. Modulestructuur & Bestanden onder `src/arb/engine/`

| Bestand | Doel |
|---|---|
| `__init__.py` | Exporteren van publieke API; leeg voor nu. |
| `tls.py` | `curl_cffi`-sessiebeheer, JA3-vingerafdrukken, HTTP/2 header-orders. |
| `proxy.py` | Proxy pool (residential + sticky ISP), health checks, failover (geïnspireerd op `changyy/py-proxy-fleet`). |
| `captcha.py` | Unified CAPTCHA-router: capsolver, 2captcha, detectie van Turnstile/hCaptcha/reCAPTCHA. |
| `adspower.py` | AdsPower localAPI-wrapper voor browserprofielen (gebaseerd op `CrocoFactory/adspower`). |
| `autocop.py` | Onbemande checkout-engine: betalingsstroom, idempotentie, P1-check. |
| `crosslister.py` | Multi-channel cross-lister: eBay REST, Depop/Poshmark/Mercari via Selenium. |
| `monitor.py` | Hoogfrequente polling workers (Vinted, eBay Browse). |
| `scheduler.py` | Asyncio-scheduler voor periodieke taken, heartbeat, staleness-detectie. |
| `config.py` | (Wordt later toegevoegd aan `src/arb/config.py`; zie §3.) |

Alle modules volgen de bestaande stijl: `from __future__ import annotations`, strikte types, geen `Any`, `ruff`-schoon.

---

## 3. Naadloze Integratiepunten in bestaande CLI

### 3.1 Feature Flags in `src/arb/config.py` (toe te voegen)

```python
# In Settings klasse:
engine_enabled: bool = Field(default=False, alias="ARB_ENGINE_ENABLED")
engine_monitor_interval_seconds: int = Field(default=5, alias="ARB_ENGINE_MONITOR_INTERVAL")
engine_autocop_enabled: bool = Field(default=False, alias="ARB_ENGINE_AUTOCOP_ENABLED")
engine_crosslister_enabled: bool = Field(default=False, alias="ARB_ENGINE_CROSSLISTER_ENABLED")
engine_proxy_pool_path: Path | None = Field(default=None, alias="ARB_ENGINE_PROXY_POOL_PATH")
engine_capsolver_api_key: str | None = Field(default=None, alias="ARB_ENGINE_CAPSOVER_API_KEY")
engine_2captcha_api_key: str | None = Field(default=None, alias="ARB_ENGINE_2CAPTCHA_API_KEY")
engine_adspower_api_url: str | None = Field(default=None, alias="ARB_ENGINE_ADSPOWER_API_URL")
```

Alle standaard `False` of `None`, zodat bestaande tests onveranderd slagen.

### 3.2 CLI Commando's (toe te voegen aan `src/arb/cli.py`)

```python
@app.command()
def engine_monitor(
    ctx: typer.Context,
    interval: int = typer.Option(5, help="Polling interval in seconds"),
) -> None:
    """Start de engine monitor (alleen als ARB_ENGINE_ENABLED=True)."""
    settings = get_settings()
    if not settings.engine_enabled:
        typer.echo("Engine is disabled. Set ARB_ENGINE_ENABLED=true.", err=True)
        raise typer.Exit(1)
    # start asyncio loop met engine.monitor.run(interval)


@app.command()
def engine_autocop(
    ctx: typer.Context,
    dry_run: bool = typer.Option(True, help="Voer droogloop uit zonder aankoop"),
) -> None:
    """Start AutoCop checkout worker."""
    # ...


@app.command()
def engine_crosslist(
    ctx: typer.Context,
    listing_id: str = typer.Argument(..., help="Listing ID uit inventory"),
) -> None:
    """Publiceer een listing naar alle geconfigureerde kanalen."""
    # ...
```

Deze commando's worden alleen actief als de corresponderende feature flag aan staat.

---

## 4. Precedent Code om te Oogsten

| Precedent | Bestand(en) / Functies | Wat te halen |
|---|---|---|
| `Pawikoski/vinted-api-wrapper` | `vinted_api/client.py`, `vinted_api/items.py` | Endpoint-shapes, authenticatie-flow, paginering. |
| `vincenzoAiello/VintedAPI` | `VintedAPI/VintedAPI.php` (Node) | Android-endpoint-patronen (stabieler dan web). |
| `herissondev/vinted-api-wrapper` | `vinted_api_wrapper/client.py` | Tweede lezing van dezelfde endpoints. |
| `JakobAIOdev/Vintrack-Vinted-Monitor` | `monitor.py`, `proxy_manager.py` | Polling-loop, proxy-rotatie, seen-set. |
| `teddy-vltn/vinted-discord-bot` | `bot/cogs/monitor.py` | Filter-schema's, notificatie-logica. |
| `changyy/py-proxy-fleet` | `proxy_fleet.py` | Load-balancing, health checks, failover. |
| `berstend/puppeteer-extra-plugin-stealth` | `puppeteer-extra-plugin-stealth/index.js` | WebGL/navigator-masking (vertalen naar Python via `curl_cffi` of Playwright). |
| `CrocoFactory/adspower` | `adspower/api.py` | LocalAPI-aanroepen voor browserprofielen. |
| `workwhileweb/Multilogin.Api` | `multilogin_api/api.py` | Alternatief voor AdsPower. |
| `capsolver-ai/capsolver-core` | `capsolver_core/capsolver.py` | CAPTCHA-oplossing, token-injectie. |
| `2captcha/2captcha-python` | `2captcha_python/solver.py` | Tweede CAPTCHA-provider. |
| `adyen-examples/adyen-node-online-payments` | `server/payments.js` | Betalingsflow: `/paymentMethods`, `/sessions`, webhook. |
| `hendt/ebay-api` | `ebay_api/sell/inventory.py` | Officiële eBay REST API voor publiceren. |
| `lyndskg/posh-a-matic` | `posh_a_matic/automation.py` | Selenium-patronen voor Poshmark. |
| `xzhou13/PoshmarkNursery` | `nursery/automation.py` | Aanvullende Poshmark-automatisering. |
| `jennyvothreads/acculister` | `acculister/crosslister.py` | Marketplace-agnostisch listing-payload. |

---

## 5. Stapsgewijze Implementatie

### Fase 1 — Monitoring & TLS/JA3 Stack (prioriteit)

1. **Maak `src/arb/engine/__init__.py`** met lege `__all__`.
2. **Implementeer `tls.py`**:
   - Klasse `TlsSession` die `curl_cffi.Curl`-sessie beheert.
   - Vooraf gedefinieerde JA3-vingerafdrukken (Chrome 120, Firefox 122).
   - Methode `request(method, url, headers, data)` die `curl_cffi` aanroept.
   - Test met `respx`-mock.
3. **Implementeer `proxy.py`**:
   - Klasse `ProxyPool` die lijst van proxies laadt uit bestand of omgevingsvariabele.
   - `get_proxy()` retourneert volgende proxy (round-robin).
   - `mark_failed(proxy)` verwijdert tijdelijk.
   - Health check via `curl_cffi` met timeout.
4. **Implementeer `monitor.py`**:
   - `async def poll_vinted(keyword, filters, tls_session, proxy_pool)`.
   - Gebruik `tls.py` voor verzoeken, `proxy.py` voor rotatie.
   - Seen-set via `listings` tabel (bestaande `upsert_listing`).
   - Stuur `ListingScannedEvent` naar event bus.
5. **Voeg feature flags toe aan `config.py`** (vraag gebruiker om bestand toe te voegen).
6. **Voeg CLI-commando `engine monitor` toe aan `cli.py`** (vraag gebruiker om bestand toe te voegen).
7. **Schrijf tests**:
   - `tests/engine/test_tls.py`
   - `tests/engine/test_proxy.py`
   - `tests/engine/test_monitor.py`
   - Zorg dat alle tests slagen met `ARB_ENGINE_ENABLED=False`.

### Fase 2 — CAPTCHA-oplossing

1. **Implementeer `captcha.py`**:
   - `solve_turnstile(site_key, page_url)` → token.
   - `solve_hcaptcha(site_key, page_url)` → token.
   - Routering naar capsolver of 2captcha op basis van config.
2. **Integreer in `tls.py`**: na detectie van CAPTCHA in response, roep `captcha.py` aan en herhaal verzoek met token.

### Fase 3 — AutoCop Checkout Engine

1. **Implementeer `autocop.py`**:
   - `async def attempt_purchase(listing, tls_session, proxy)`.
   - Stap 1: haal `/paymentMethods` op.
   - Stap 2: maak sessie aan (`/sessions`).
   - Stap 3: tokeniseer betaalmiddel.
   - Stap 4: voer betaling uit.
   - Gebruik `autobuy.py`-rails: roep `Authorisation.can_purchase()` aan.
   - Schrijf `PurchaseAttempts`-rij.
2. **Integreer met CLI**: `arb engine autocop`.

### Fase 4 — Stealth Browser Fleet (AdsPower)

1. **Implementeer `adspower.py`**:
   - `create_profile(name, proxy)`.
   - `start_profile(profile_id)` → WebSocket URL.
   - `close_profile(profile_id)`.
2. **Optioneel**: Playwright-integratie voor taken die Selenium vereisen (cross-lister).

### Fase 5 — Cross-Lister

1. **Implementeer `crosslister.py`**:
   - `publish_to_ebay(listing_draft)` via `ebay_rest`.
   - `publish_to_depop(listing_draft)` via Selenium.
   - `publish_to_poshmark(listing_draft)` via Selenium.
   - `publish_to_mercari(listing_draft)` via Selenium.
   - `auto_delist(item_id)` bij status 'Sold'.
2. **Integreer met CLI**: `arb engine crosslist`.

### Fase 6 — Hardening & Plaatsvervangerregistratie

- Voeg P11 (TLS-profiel), P12 (CAPTCHA-kosten), P13 (proxy-betrouwbaarheid) toe aan plaatsvervangerregister in `provenance.py`.
- Werk `arb provenance` bij om engine-gerelateerde aannames te rapporteren.

---

## 6. Kwaliteitspoort

Alle nieuwe code moet voldoen aan:

- `ruff check src/arb/engine/` — geen fouten.
- `mypy --strict src/arb/engine/` — geen fouten, geen `Any`.
- `scripts/guard.py` — geen onderdrukkingen, geen handgeschreven `Any`.
- `pytest tests/engine/ --cov=src/arb/engine --cov-fail-under=85`.
- Bestaande 550 tests blijven slagen (feature flags uit).

---

## 7. Volgende Stappen

1. Vraag gebruiker om `src/arb/config.py` en `src/arb/cli.py` toe te voegen aan chat voor bewerking.
2. Begin met Fase 1: implementeer `tls.py`, `proxy.py`, `monitor.py`.
3. Schrijf tests voor elke module.
4. Voeg feature flags en CLI-commando's toe.
5. Herhaal voor volgende fasen.

Dit plan is een levend document; werk het bij na elke fase.
