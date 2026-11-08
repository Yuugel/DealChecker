# DealChecker MVP design

Authority: ../../project-brief.md. User explicitly requested autonomous implementation with Sol/Luna workers and only necessary questions. Routine architecture and implementation choices below are lead decisions.

## Inspection
Yuugel/DealChecker was empty (no branches, commits, files, conventions or tests), cloned to C:/Users/hasan/Meine Projekte/DealChecker. No existing changes to preserve. Hero Line Wars is unrelated and remains untouched.

## Approach
A single local Python application with FastAPI, SQLite WAL, an asyncio scheduler and static HTML/CSS/JS offers a portable Windows/Linux implementation. A desktop-native app complicates Linux migration; a cloud/microservice architecture conflicts with local operation. No LLM or account service is required.

## Interfaces and ownership
- `models.py`: Pydantic WatchInput/Watch and Offer models; cents, UTC epoch timestamps.
- `db.py`: Database persistence/migrations for watches, latest offers, immutable observations, events, checks, settings and notification outbox. Bound SQL only. Atomic check commits.
- `service.py`: Engine executes discovery or exact lookups, filters, deduplicates, evaluates and persists; `scheduler.py` bounded tasks and durable scheduling.
- `network.py`: fixed/configured HTTPS endpoints, public-IP checks, DNS pinning, response-size/time limits, no redirects, per-host rate limits.
- `adapters/`: registry with async `search(watch)` and `lookup(watch)` returning list[Offer]. Real documented no-key source selected after research; explicitly configured authorized merchant JSON feeds extend all categories. No blind marketplace scraping.
- `notifications.py`: Discord webhook via environment variable, durable retries and dedupe; local events always work. No unsolicited test send without user-configured channel.
- `app.py`: local API and static assets; strict Host/Origin checks and JSON mutations, no credentials exposed.
- `static/`: responsive German dashboard, watch editor, offer cards/history, events, checks and settings.

## Behavior
Exact and discovery watches are distinct. Discovery issues fresh source searches, including synonyms, and filters manufacturer/exclusions/category/price/merchant. Exact matching uses source IDs, GTIN/model/variant where supported and never assumes fuzzy titles identify variants. Global search across arbitrary retailers is not promised. Source-specific unsupported identity fields are reported.

Prices are integer cents in EUR. Unknown shipping means unknown total and no total-price alarm. Comparisons use prior observations for identical source/product/variant and only actual data. Initial observations can meet a target but cannot claim historical lows or market savings. Failed checks retain last-known prices and expose age/error. Observations contain availability, source, provenance and timestamps. Historical medians are labelled observed historical comparisons, not market prices.

Priorities: critical 300s, high 900s, normal 1800s, low 3600s; per-watch override >=300s. Temporary Black Friday mode halves intervals with a 300s floor and automatic expiry. Source rate limits remain in force. Manual checks obey source limits. Bounded concurrency, per-watch exclusion, timeout, persisted next_due/failure count, exponential backoff, startup recovery and graceful cancellation.

## Boundaries
External notification delivery cannot be certified without credentials. Real price movement over time cannot be fabricated: verify current live fetch and controlled change fixtures separately. Windows live startup is tested; Linux readiness is covered by portable code and CI, not claimed as an executed Linux deployment. Autostart remains optional explicit installation. No paid integrations, live purchases or global environment changes.
