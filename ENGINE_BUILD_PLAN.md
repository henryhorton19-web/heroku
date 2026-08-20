# ENGINE BUILD PLAN -- High-Throughput Reselling & Arbitrage Engine

## 1. Architecture & Event Bus Flow

Event Bus: Use asyncio.Queue for internal coordination.
Polling Workers: Execute in isolated asyncio tasks, each with distinct proxy rotation and TLS profiles.

## 2. Module Structure & Files under src/arb/engine/
- tls.py: curl_cffi session management, JA3 fingerprinting, HTTP/2 header ordering.
- proxy.py: Proxy pool (residential + sticky ISP), health checks, failover.
- captcha.py: Unified CAPTCHA router (capsolver, 2captcha).
- adspower.py: AdsPower localAPI wrapper for browser profiles.
- autocop.py: Unattended checkout engine (payment flow, idempotency, P1 check).
- crosslister.py: Multi-channel cross-lister (eBay REST, Depop/Poshmark/Mercari via Selenium).
- monitor.py: High-frequency polling workers (Vinted, eBay Browse).
- scheduler.py: Asyncio scheduler for periodic tasks.

## 3. Integration Points
Feature flags in src/arb/config.py: ARB_ENGINE_ENABLED, ARB_ENGINE_AUTOCOP_ENABLED, etc. (all default False).

## 4. Quality Gate Compliance
All code must be ruff clean, mypy strict clean, pass scripts/guard.py, and maintain 85%+ coverage.
