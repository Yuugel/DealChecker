# Review fixes 2 and 3

## Changes

- A fresh, known, in-stock total may alert after an old history entry. Once alerted, it must be strictly below both the last alerted total and the most recent eligible observed total. Replayed, rising, or stale current observations remain silent. Older unknown or out-of-stock rows do not veto a later reduction.
- Schema 4 adds `watch_sources` with one persisted due time, failure count, last error, and check time per watch and source. A watch's public `next_due` is the earliest source due. Successful sources retain normal cadence while failing sources back off exponentially (up to 16 times). Schema-3 successful watch due times are preserved. Schema-3 failed watches are due for a prompt recheck because their single error cannot identify the failed source; the old watch error remains visible until that check. Migration DDL and data copy are one explicit transaction and roll back together if interrupted.
- Scheduled checks fetch only due sources. Manual checks re-fetch healthy sources for immediate feedback while respecting failed-source backoff; the shared guarded HTTP client continues to enforce transport limits. Errors from skipped failed sources remain visible in the watch and check result. Source fetches run concurrently, with source and whole-check 60-second deadlines. The 20-source WatchInput bound caps per-watch fan-out.
- Offers become stale according to their own watch/source check and due state. An unrelated source's due time or another watch's error cannot stale a healthy offer. Check observations, events, source schedules, aggregate watch state, and source status are committed together.
- Editing a watch preserves the failure schedule for retained sources, starts new sources due immediately, and clears errors for removed sources. Black Friday can advance each source's due time; pause suppresses ticks without discarding schedules.

## Verification

- Synthetic SQLite regressions cover four-hour aged history with reopen, intervening unknown/out-of-stock rows, previous eligible price, repeated good/bad cycles and restart, unchanged-price deduplication, a hung first source, stale state, pause/edit/Black Friday, schema-3 migration, and interrupted migration rollback.
- `python -m pytest -q`: 93 passed; one Starlette deprecation warning from the installed FastAPI test client.
- `python -m compileall -q dealchecker`: passed.
- `git diff --check`: passed.

No live merchant requests or browser checks were made. Manual checks intentionally allow immediate healthy-source refresh for the existing API workflow; actual network requests still use the shared guarded client and its per-host rate limit.
