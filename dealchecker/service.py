"""Bounded, single-flight watch checks and observed-price evaluation."""
import asyncio
import statistics
import time

from .db import Database, interval
from .models import Offer, WatchInput


def _contains(text: str, term: str) -> bool:
    return term.casefold() in text.casefold()


def _matches(watch: WatchInput, offer: Offer) -> bool:
    if watch.kind == "exact":
        if watch.product_id and offer.product_id.casefold() != watch.product_id.casefold():
            return False
        for field in ("gtin", "model", "variant"):
            expected = getattr(watch, field)
            actual = getattr(offer, field)
            if expected:
                if not actual:
                    raise ValueError(f"source does not provide requested {field}")
                if actual.casefold() != expected.casefold():
                    return False
        if not any((watch.product_id, watch.url, watch.gtin, watch.model, watch.variant)) and watch.query:
            if offer.name.casefold() != watch.query.casefold():
                return False
    else:
        terms = [watch.query, *watch.synonyms]
        if not any(_contains(" ".join((offer.name, offer.model, offer.manufacturer, offer.category)), t) for t in terms):
            return False
    if watch.manufacturer and not _contains(offer.manufacturer, watch.manufacturer):
        return False
    if watch.category and not _contains(offer.category, watch.category):
        return False
    if any(_contains(" ".join((offer.name, offer.model, offer.variant)), x) for x in watch.exclusions):
        return False
    if watch.merchants_include and not any(_contains(offer.merchant, x) for x in watch.merchants_include):
        return False
    if any(_contains(offer.merchant, x) for x in watch.merchants_exclude):
        return False
    if watch.max_price_cents is not None and (offer.total_cents is None or offer.total_cents > watch.max_price_cents):
        return False
    return True


def _evaluate(watch: WatchInput, offer: Offer, history: list[dict], last_alert: int | None,
              interval_seconds: int, now: float):
    previous = [h["total_cents"] for h in history if h["total_cents"] is not None and h["availability"] == "in_stock"]
    comparison = min(previous) if previous else None
    savings = max(0, comparison - offer.total_cents) if comparison is not None and offer.total_cents is not None else None
    if offer.total_cents is None or offer.availability != "in_stock":
        return "Unzureichende Daten", comparison, savings, None
    fresh = now - offer.checked_at <= 2 * interval_seconds and offer.checked_at <= now + 60 and offer.quality != "feed_stale"
    if history and offer.checked_at <= history[-1]["checked_at"]:
        fresh = False
    if not fresh:
        return "Veraltete Daten", comparison, savings, None
    reason = None
    if watch.target_cents is not None and offer.total_cents <= watch.target_cents:
        reason = "Wunschpreis erreicht"
    elif comparison is not None and offer.total_cents < comparison:
        reason = "Neuer Bestpreis"
    elif len(previous) >= 3 and offer.total_cents <= .9 * statistics.median(previous):
        reason = "Sehr gutes Angebot"
    elif previous and offer.total_cents <= .95 * previous[-1]:
        reason = "Interessanter Preisrückgang"
    evaluation = reason or ("Unzureichende Daten" if not history else "Kein relevantes Angebot")
    if reason and last_alert is not None:
        last = history[-1] if history else None
        prev_eligible = last and last["total_cents"] is not None and last["availability"] == "in_stock" and now - last["checked_at"] <= 2 * interval_seconds
        if not prev_eligible or offer.total_cents >= last_alert or offer.total_cents >= last["total_cents"]:
            reason = None
    return evaluation, comparison, savings, reason


class Engine:
    def __init__(self, db: Database, adapters: dict, notifier=None):
        self.db = db
        self.adapters = adapters
        self.notifier = notifier
        self._running: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(4)
        self._delivery_lock = asyncio.Lock()
        self._closed = False

    def is_running(self, id: int) -> bool:
        return id in self._running

    async def check_watch(self, id: int) -> dict:
        if self._closed:
            return {"status": "error", "offers": 0, "error": "engine closed"}
        watch_row = self.db.get_watch(id)
        if watch_row is None:
            return {"status": "missing", "offers": 0, "error": None}
        if self.db.get_settings()["paused"] or watch_row["paused"]:
            return {"status": "paused", "offers": 0, "error": None}
        if id in self._running:
            return {"status": "busy", "offers": 0, "error": None}
        self._running.add(id)
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            async with self._sem:
                return await self._check(id, watch_row)
        finally:
            self._running.discard(id)
            if task is not None:
                self._tasks.discard(task)

    async def _check(self, id: int, row: dict) -> dict:
        watch = WatchInput.model_validate({k: v for k, v in row.items() if k in WatchInput.model_fields})
        results: list[Offer] = []
        errors = []

        async def fetch():
            for source in watch.source_ids:
                adapter = self.adapters.get(source)
                if adapter is None:
                    errors.append(f"{source}: source unavailable")
                    continue
                try:
                    method = adapter.search if watch.kind == "discovery" else adapter.lookup
                    found = await method(watch)
                    if not isinstance(found, list):
                        raise ValueError("invalid source result")
                    for offer in found:
                        if not isinstance(offer, Offer):
                            raise ValueError("invalid offer")
                        if offer.source != source:
                            raise ValueError("source identity mismatch")
                        results.append(offer)
                except (ValueError, RuntimeError, OSError) as exc:
                    errors.append(f"{source}: {str(exc)[:200]}")
                except Exception as exc:
                    errors.append(f"{source}: {type(exc).__name__} failure")

        try:
            await asyncio.wait_for(fetch(), timeout=60)
        except asyncio.TimeoutError:
            errors.append("check deadline exceeded")
        latest = self.db.get_watch(id)
        if latest is None:
            return {"status": "missing", "offers": 0, "error": None}
        if latest["paused"] or self.db.get_settings()["paused"]:
            return {"status": "paused", "offers": 0, "error": None}
        if latest != row:
            return {"status": "error", "offers": 0, "error": "watch changed during check"}
        now = time.time()
        unique = {}
        for item in results:
            try:
                if _matches(watch, item):
                    if item.key not in unique or item.checked_at >= unique[item.key].checked_at:
                        unique[item.key] = item
            except ValueError as exc:
                errors.append(f"{item.source}: {str(exc)[:200]}")
        if watch.kind == "exact" and watch.query and not any((watch.product_id, watch.url, watch.gtin, watch.model, watch.variant)) and len(unique) > 1:
            errors.append("exact title is ambiguous")
            unique.clear()
        if watch.kind == "exact" and not unique and not errors:
            errors.append("exact product not found")
        observations = []
        for item in unique.values():
            history, last_alert, _ = self.db.prior(id, item.key)
            observations.append((item, *_evaluate(watch, item, history, last_alert, interval(row, self.db.get_settings()), now)))
        status = "error" if errors else "ok"
        error = "; ".join(errors) if errors else None
        self.db.record_check(id, observations, status, error, now)
        await self.flush_outbox()
        return {"status": status, "offers": len(observations), "error": error}

    async def flush_outbox(self):
        if not self.notifier or not self.db.get_settings()["notification_enabled"]:
            return
        async with self._delivery_lock:
            for event in self.db.due_outbox():
                try:
                    await asyncio.wait_for(self.notifier.send(event), timeout=15)
                except Exception:
                    self.db.outbox_result(event["id"], "notification delivery failed")
                else:
                    self.db.outbox_result(event["id"], None)

    async def tick(self) -> None:
        if self._closed:
            return
        if not self.db.get_settings()["paused"]:
            due = [w for w in self.db.due_watches() if w["id"] not in self._running][:4]
            await asyncio.gather(*(self.check_watch(w["id"]) for w in due))
        await self.flush_outbox()

    async def close(self) -> None:
        self._closed = True
        if self._tasks:
            tasks = set(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
