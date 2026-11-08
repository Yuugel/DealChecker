# Core implementation report (2026-11-08)

Scope: `models.py`, `db.py`, `service.py`, `scheduler.py`, `notifications.py`, and synthetic `test_core.py`.

## Behavior

- Pydantic v2 validates watch identity, source-scoped IDs/URLs, bounded input, strict integer EUR cents, finite timestamps and offer fields. Source adapters are injected; discovery passes query plus up to five synonyms to each adapter once.
- SQLite schema version 1 is created in an explicit transaction with WAL, FK and busy timeout. Each check commits latest offers, immutable observations, checks, local events, outbox entries, due time and failure state atomically. Reopening preserves state; a future schema version fails closed.
- Schema version 2 adds a unique `(offer_id, checked_at)` observation index and removes prior duplicate timestamp replays during migration. Repeated cached responses no longer inflate history or median sample counts.
- Schema version 3 persists each source's last check, last success and last error independently. Empty successful searches remain healthy; partial failures retain the failed source's error across restart without marking a successful source stale. Each offer also stores when it was last seen in a completed check; previously matched offers turn stale when a later check no longer returns an eligible match.
- One check per watch runs at a time. Four checks can run concurrently; each source sequence has a 60-second deadline. Partial failures save successful offers, record source errors and apply capped exponential watch backoff. Edits/deletes/pauses during adapter awaits cannot attach obsolete offers.
- Latest offers become stale on a due check, failed check for their source, unmatched later check, old observation (>2× interval), or `feed_stale` quality. Older source timestamps remain in history but cannot replace current offers or generate alerts. A matching cached replay refreshes the last-seen time without adding a historical price sample. History is sorted by source observation timestamp.
- Feed observations become stale after 600 seconds regardless of the watch interval. That same cutoff blocks alerts from an old cached feed result.
- Alert decisions use only prior known in-stock totals for the same source, merchant, product ID and variant. First observations can meet a target. Follow-up alerts require a new total below both the previous eligible observation and the last alerted total. Local event uniqueness is durable; outbox delivery is at-least-once with serialized sends and capped exponential retry.
- Each outbox flush sends at most one due event with a 15-second send timeout, keeping large event bursts from blocking a check while the scheduler drains them over later ticks.
- Events carry source, provenance and quality. Discord availability text attributes feed and live source claims rather than presenting them as independently verified stock.
- `Scheduler.start()` returns promptly; `stop()` drains an active tick. Unexpected tick exceptions are logged and do not terminate scheduling. Black Friday activation advances due times; priority/default intervals remain 300/900/1800/3600 seconds with a 300-second floor.

## Verification

Synthetic tests: 32 passed via `.venv/Scripts/python.exe -m pytest -q tests/test_core.py`; `.venv/Scripts/python.exe -m compileall -q dealchecker` passed. No real merchant prices or Discord sends were used.

## Integration notes

- API may use `Engine.is_running(watch_id)` to return HTTP 409 on edit/delete during a check. The engine also re-reads watch state before persistence.
- `Database.list_events()` includes `notification_status`, `notification_attempts`, and `notification_error` (`disabled` for local-only events).
- The application must own its single-process DB lock as specified in `contracts.md`; this core does not implement cross-process locking.
- Source errors supplied by adapters are expected to be secret-free per the adapter contract. Unexpected exceptions expose only their type.
- Discord outbox semantics are at-least-once: a process crash after Discord accepts a message but before the sent mark can cause a duplicate delivery.
