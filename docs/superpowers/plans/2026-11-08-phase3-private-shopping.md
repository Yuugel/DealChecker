# Phase 3: private shopping implementation

**Goal:** Real shopping products through supported links and bounded public search, without affiliate accounts or a commercial website.

**Approach:** Extend the current FastAPI/SQLite application and adapter contract. Use normal publicly served product data only where reviewed conditions and robots rules permit the requested private use; stop on access restrictions. No browser stealth, arbitrary crawling, purchases, accounts or paid activation. User explicitly requests autonomous delivery, so no additional design approval round.

- [ ] Sol: Alternate and BerryBase adapters; shared public-page robots guard and JSON-LD parsing; actual products and regression tests.
- [ ] Sol: Lidl public product-link adapter after reviewing conditions, variants and embedded delivery data; search only if robots permit a concrete route.
- [ ] Parent: bounded guarded text HTTP, direct-link resolve API with fixed known hosts and explicit unsupported reasons, source capabilities, separate reference budget, seller filters and canonical GTIN matching.
- [ ] Luna: URL/name entry, preview/select/monitor flow, reference budget and seller controls, source capability/error presentation.
- [ ] Parent: private-use terms finding, 7h/45min normal retail defaults while preserving existing intervals, accessible notification setup where safe.
- [ ] Integrate worker commits, independent review, full synthetic suite, real A/B/C shopping probes, browser and Windows restart checks. Keep live and synthetic evidence separate.
- [ ] Commit/push new feature branch; PR against main with actual successes and gaps, complete Windows/Linux CI, do not merge.

**Key interfaces:** `SafeHttpClient.get_text(url, allowed_hosts=..., max_body=...)`; adapters `url_hosts` and `supports_search`; `POST /api/links/resolve {url}` returns `{source_id, source_name, offers, warnings}` without creating a watch. Existing watch API creates selected exact offers. New `reference_max_price_cents`, `sellers_include`, `sellers_exclude` are optional validated watch fields. Reference prices never cause purchase alarms.

**Review focus:** Untrusted/ambiguous URLs and redirects; changed robots or blocked pages; multiple sellers/variants; unknown/conditional shipping; preview/save identity and cache age; migrations preserve user schedules; no secrets in API state/logs/Git.
