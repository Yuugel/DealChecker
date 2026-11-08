# Frozen MVP interfaces

All timestamps UTC epoch seconds; amounts integer EUR cents. JSON uses snake_case.

## Models (`dealchecker.models`)
Pydantic v2 models:
- `WatchInput`: name str; kind Literal[exact,discovery] default exact; query str default ''; source_ids list[str] default ['apple']; product_id str default ''; url str default ''; gtin str default ''; model str default ''; variant str default ''; synonyms list[str] default []; category str default ''; manufacturer str default ''; exclusions list[str] default []; merchants_include/exclude list[str] default []; target_cents int|None default None; max_price_cents int|None default None; priority Literal[critical,high,normal,low] default normal; interval_seconds int|None default None (>=300 <=86400); paused bool default False. At least name, and either query/product_id/url/gtin/model. Exact query-only matching is strict name; unknown variant/GTIN support must fail visibly, never silently ignore. Strings trimmed and bounded; list lengths <=20.
- `Watch(WatchInput)`: id int; next_due float; failure_count int=0; last_error str|None=None; last_checked float|None=None; running bool=False.
- `Offer`: source str; merchant str; product_id str; name str; url str; price_cents int>=0; shipping_cents int|None; currency Literal['EUR']='EUR'; availability Literal['in_stock','out_of_stock','unknown']; checked_at float; variant str=''; gtin str=''; model str=''; manufacturer str=''; category str=''; coupon str|None=None; unit_price str|None=None; quality str='source_reported'; provenance str='live'. `total_cents` computed property/serialized field returns None if unknown shipping else price+shipping. `key` computed stable source/product_id/variant identifier. No test/demo source enabled in normal runtime.

## Adapter / transport
`class Adapter`: id, name, description, min_interval_seconds; `async search(watch: WatchInput) -> list[Offer]`; `async lookup(watch: WatchInput) -> list[Offer]`. Adapter registry dict[str,Adapter]. A source error raises ValueError/RuntimeError with secret-free text.
`dealchecker.network.SafeHttpClient(min_interval=3.2)`: `async get_json(url: str, *, allowed_hosts: set[str]|None=None) -> dict|list`; `async post_json(url: str, payload: dict, *, allowed_hosts: set[str]|None=None) -> None`; `async close()`. Requests HTTPS only, public DNS pinned, no redirects, no URL userinfo/fragments/custom ports, max 2MiB, timeout 15s, per-host serialization/rate limit.
`dealchecker.adapters.build_registry(client, feeds: list[dict]|None=None) -> dict`. Apple id 'apple'. Feed configs {id, name, url}, env-only for authorized JSON catalog feeds. `source_catalog(registry)` returns [{id,name,description,min_interval_seconds,configured: true}].

## Core persistence / engine
`Database(path: str|Path)` initializes dirs/schema (WAL, FK, busy timeout), methods:
- `create_watch(data: WatchInput) -> dict`, `update_watch(id, data: WatchInput)->dict`, `delete_watch(id)->bool`, `get_watch(id)->dict|None`, `list_watches()->list[dict]`.
- `list_offers(watch_id: int|None=None)->list[dict]`: latest rows include id, watch_id, Offer fields, total_cents, stale bool, evaluation str, comparison_cents int|None, savings_cents int|None. Stale means last successful offer is older than current due/error state, or >2*interval; retained after failures.
- `history(offer_id:int)->list[dict]`: checked_at, price_cents, shipping_cents, total_cents, availability, provenance. Immutable; chronological.
- `list_events()->list[dict]`, `list_checks()->list[dict]` newest first, <=200.
- `get_settings()->dict`: paused False, black_friday_until 0, notification_enabled False. `update_settings(dict)->dict` accepts these keys only.
- `close()` closes owned resources. Each sync transaction atomic; no awaiting inside transactions.
Core may add private/public helpers as necessary. `Engine(db, adapters, notifier=None)`:
- `async check_watch(id:int)->dict`: returns {status:'ok'|'error'|'busy'|'paused'|'missing', offers:int, error:str|None}. Single-flight per watch including manual. Timed bounded checks, filter offers, calculate comparisons from prior history, atomic observations/events/outbox and next_due. Any source error visible in checks + watch; successful source offers retained. No unsupported sources silently skipped. Paused watches/global pause do not run manually.
- `async tick()->None`: start due checks with bounded concurrency, ignore running watches; persistent next_due and failure backoff. Tests can await completion.
- `async close()->None`: cancellation/drain.
`Scheduler(engine)`: `async start()` starts background task and returns promptly; `async stop()` drains/stops. Tick every 5s. Persist next_due for restarts. App owns one process; additional process using same DB prohibited via application lock.
`DiscordNotifier(client, webhook_url: str)`: `async send(event:dict)->None`. Event fields product/name, merchant, total_cents, comparison_cents, savings_cents, url, checked_at, availability, reason. Restrict discord.com/discordapp.com webhook paths; redact errors, allowed_mentions empty. Durable outbox retry driven by Engine; notification_enabled default false. Never configure fake credentials.

## HTTP routes / UI contract
- GET /api/state -> {watches,offers,events,checks,settings,sources,notification_configured:bool}; source catalog includes last_error/last_checked when available.
- POST /api/watches JSON WatchInput -> watch (201).
- PUT /api/watches/{id} JSON WatchInput -> watch.
- DELETE /api/watches/{id} -> {deleted:true} (404 if absent).
- POST /api/watches/{id}/check JSON {} -> Engine result; may await up to check timeout.
- GET /api/offers/{id}/history -> list of observations.
- PUT /api/settings JSON {paused?,black_friday_until?,notification_enabled?} -> settings. BF <=24h future; disable by 0.
- GET /api/health -> {status:'ok',version:'0.1.0'}.
JSON mutation headers Content-Type application/json; no cookies required; strict Host localhost/127.0.0.1/[::1], Origin same host and port (if present). No permissive CORS. UI fetch helper sends JSON. GET / serves static index; /static/app.js,/static/style.css.

## Core deal rules
Use same offer key+variant history only. In-stock + known total required for alert. Target <= target_cents triggers 'Wunschpreis erreicht' even first observation. Otherwise strictly below historical minimum (at least one prior observation) -> 'Neuer Bestpreis'; <=90% median of >=3 previous known/in-stock observations -> 'Sehr gutes Angebot'; decrease >=5% from previous -> 'Interessanter Preisrückgang'; otherwise 'Kein relevantes Angebot' or 'Unzureichende Daten'. No unobserved market price. Deduplicate event by watch+offer key+total; unchanged/rising prices cannot renotify. Relevant further reductions can. Persistence of all observations including unchanged checks is acceptable. Filters case-insensitive; discovery query synonyms OR; excludes/include merchants, category/manufacturer AND. Exact IDs authoritative; variant/GTIN/model must match if requested and available, else raise source capability error.
