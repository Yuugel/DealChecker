# Phase 2: useful German-market coverage

The user authorizes autonomous design, delegation, implementation, tests and a new PR against main, without merging. Baseline is merged PR #1, main `0149900`; all 116 baseline tests pass. Existing sources are Apple and the explicitly authorized Modula24 furniture catalog.

## Chosen approach

Use documented APIs and explicitly authorized catalogs. Research 20 named major sources plus plausible alternatives; test public read access without bypassing authentication, rate limits or terms. Established retail data mostly needs publisher approval. Implement credible free discovery even where it only provides price references, label its limitations, and keep reference prices out of purchase alarms. Prepare at most one useful publisher integration if access is missing; never advertise unconfigured retailers as working.

Rejected alternatives: undocumented marketplace scraping creates access/reliability problems; installing many empty retailer adapters would not increase actual coverage. Free-looking APIs from untrustworthy operators are not useful integration targets.

## Architecture and contracts

Keep Python 3.11+, FastAPI, SQLite and plain JS. Integer EUR cents, unknown shipping remains unknown, guarded HTTPS, loopback and no secrets in logs/API/Git remain mandatory. Separate fixtures from live evidence.

### Scheduling

New effective defaults: discovery 8 hours normal / 3 hours Black Week; exact normal 2 hours / 30 minutes; important (`high`) 45 / 10 minutes; critical 30 / 5 minutes; low 8 hours / 1 hour. Broad discovery is not accelerated merely by a priority flag. Explicit `interval_seconds` remains normal override. Add `black_friday_interval_seconds` (300–86400) and `source_intervals`, mapping selected source IDs to `{normal_seconds, black_friday_seconds}` within those same bounds. Source-specific overrides take precedence over watch overrides; adapter limits take precedence over all. Provide Amazon 2h/20m and MediaMarkt/Saturn 7h/45m as documented configurable examples, not enabled adapters.

Preserve existing watches' effective prior defaults during schema migration by populating their normal/Black-Friday overrides if missing. Existing explicit normal overrides retain the former half-interval Black-Friday behavior unless a new value is set. New watches receive the new adaptive defaults. Editing intervals or enabling/disabling Black Week must recalculate source due times without erasing failures or causing early retries. Persist source minimum intervals for rescheduling; expire Black Week correctly. Retain the existing `black_friday_until` wire name but allow up to seven days (UI chooses 24h or seven days). The minimum interval is always 300 seconds.

### Identity and comparison

Extend `Offer` with optional seller, pack_size and condition (new/used/refurbished/unknown) and offer_type (retail/reference); retain old keys when optional identity values are absent. A marketplace seller/pack/condition change must not share incompatible price history. Generic authorized feeds may supply these optional fields. Do not fill these fields from unverified guesses.

Create conservative grouping from valid checksum-verified canonical GTIN; fallback manufacturer+model requires explicit matching variant/pack information. Conflicting manufacturer/model/variant/pack/condition splits or rejects comparison. Similar names never establish identity. Include only fresh in-stock retail offers with known total in delivered-price rankings. Reference prices are clearly labelled and never generate total-price alarms or fabricated shipping. Cross-merchant comparison is a separate contemporaneous display; historical alarms retain their existing same-offer meaning. `/api/state` exposes a `comparisons` list of safe groups with identity basis, offer IDs and lowest delivered total; no evidence means no group.

### Practical workflow and sources

The UI can turn a found offer into a new exact watch, preserving source and variant identity, with editable normal/Black-Week intervals. Display seller, pack, reference status and the true effective source schedule. Broad discovery is separate from selected-product monitoring.

Bestenvergleich: fixed official search and product/EAN API, attributed product links, 120/min upper limit, conservative cache (20 minutes). Its API price_min omits shop/ship data, so use reference type, unknown shipping and no purchase alarm. Products not evidenced by the API are not invented.

Modula24: discovery keeps bounded catalog caching. Known product URLs use documented per-product JSON plus read-only shipping quotation for one unit to Germany; preserve standard DHL shipping policy and validate the requested numeric variant. ID-only lookup can bootstrap URL from the catalog once. Cache bounded product/quote results without redating cache hits; do not fetch the entire catalog for every known-product update.

## Validation and delivery

Tests first for scheduling, migration, matching/marketplace separation, reference gating, caches/targeted requests and API/UI integration. Verify every activated new source live, separately from synthetic price movement tests. Run the whole suite, compileall, JS syntax and CI on Windows/Ubuntu Python 3.11/3.14. Review changed code independently. Document the 20-source matrix, real probes, remaining access blockers and actual coverage. Push a new feature PR; no merge or paid activation.
