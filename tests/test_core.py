"""Synthetic core fixtures: these are not real merchant offers."""
import asyncio
import time

import pytest
from pydantic import ValidationError

from dealchecker.models import Offer, WatchInput
from dealchecker.db import Database
from dealchecker.service import Engine
from dealchecker.scheduler import Scheduler
from dealchecker.notifications import DiscordNotifier


def watch(**kw):
    return WatchInput(**{"name": "Synthetic Widget", "query": "Synthetic Widget", "source_ids": ["synthetic"], **kw})


def offer(price=1000, shipping=200, **kw):
    kw.setdefault("checked_at", time.time())
    return Offer(source="synthetic", merchant="Synthetic Shop", product_id="s1",
                 name="Synthetic Widget", url="https://example.org/item", price_cents=price,
                 shipping_cents=shipping, availability="in_stock", **kw)


class Adapter:
    id = "synthetic"
    name = "Synthetic"
    description = "Fixture only"
    min_interval_seconds = 0

    def __init__(self, results=None):
        self.results = results or []
        self.calls = 0

    async def lookup(self, watch):
        self.calls += 1
        return self.results

    search = lookup


def test_models_validate_and_serialize():
    assert offer().model_dump()["total_cents"] == 1200
    assert offer(shipping=None).total_cents is None
    with pytest.raises(ValidationError):
        WatchInput(name="x", source_ids=["a"])
    with pytest.raises(ValidationError):
        watch(interval_seconds=299)
    with pytest.raises(ValidationError):
        watch(source_ids=["a"] * 21)
    with pytest.raises(ValidationError):
        offer(price=-1)
    with pytest.raises(ValidationError):
        offer(price=True)
    with pytest.raises(ValidationError):
        offer(checked_at=float("nan"))
    with pytest.raises(ValidationError):
        watch(product_id="123", source_ids=["one", "two"])
    with pytest.raises(ValidationError):
        watch(synonyms=["x"] * 6)
    assert offer().key != offer().model_copy(update={"product_id": "S1"}).key
    assert offer().key != offer().model_copy(update={"variant": "a\x1fb"}).key
    assert offer().key != offer().model_copy(update={"merchant": "Other Shop"}).key


def test_database_crud_settings_migration_reopen(tmp_path):
    path = tmp_path / "nested" / "core.db"
    db = Database(path)
    row = db.create_watch(watch())
    assert row["id"] > 0 and row["next_due"] > 0
    assert db.get_watch(row["id"])["name"] == "Synthetic Widget"
    assert db.update_watch(row["id"], watch(target_cents=500))["target_cents"] == 500
    assert db.get_settings() == {"paused": False, "black_friday_until": 0, "notification_enabled": False}
    assert db.update_settings({"notification_enabled": True})["notification_enabled"] is True
    with pytest.raises(ValueError):
        db.update_settings({"unknown": True})
    db.close()
    reopened = Database(path)
    assert reopened.get_watch(row["id"])["target_cents"] == 500
    assert reopened.get_settings()["notification_enabled"] is True
    assert reopened.schema_version > 0
    assert reopened.delete_watch(row["id"]) is True
    assert reopened.delete_watch(row["id"]) is False
    reopened.close()


@pytest.mark.asyncio
async def test_history_evaluation_and_monotonic_dedupe(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(target_cents=1500))
    adapter = Adapter([offer(1000)])
    engine = Engine(db, {"synthetic": adapter})
    assert (await engine.check_watch(row["id"]))["status"] == "ok"
    events = db.list_events()
    assert len(events) == 1 and events[0]["reason"] == "Wunschpreis erreicht"
    adapter.results = [offer(1000)]
    await engine.check_watch(row["id"])
    adapter.results = [offer(1100)]
    await engine.check_watch(row["id"])
    adapter.results = [offer(900)]
    await engine.check_watch(row["id"])
    assert len(db.list_events()) == 2
    latest = db.list_offers(row["id"])[0]
    history = db.history(latest["id"])
    assert [x["price_cents"] for x in history] == [1000, 1000, 1100, 900]
    assert latest["comparison_cents"] == 1200 and latest["savings_cents"] == 100
    await engine.close()
    db.close()


@pytest.mark.asyncio
async def test_unknown_total_stale_observation_and_filter(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(max_price_cents=1500))
    adapter = Adapter([offer(500, None)])
    engine = Engine(db, {"synthetic": adapter})
    assert (await engine.check_watch(row["id"]))["offers"] == 0
    assert db.list_events() == []
    adapter.results = [offer(500, 0, checked_at=time.time() - 10000)]
    await engine.check_watch(row["id"])
    assert db.list_offers(row["id"])[0]["stale"] is True
    assert db.list_events() == []
    await engine.close()


@pytest.mark.asyncio
async def test_partial_error_persists_success_backoff_and_staleness(tmp_path):
    class Broken(Adapter):
        async def lookup(self, watch):
            raise RuntimeError("source unavailable")

    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch().model_copy(update={"source_ids": ["synthetic", "broken"]}))
    engine = Engine(db, {"synthetic": Adapter([offer()]), "broken": Broken()})
    result = await engine.check_watch(row["id"])
    assert result["status"] == "error" and result["offers"] == 1
    assert "broken" in result["error"]
    assert db.get_watch(row["id"])["failure_count"] == 1
    assert db.list_offers(row["id"])[0]["stale"] is True
    assert db.list_checks()[0]["status"] == "error"
    await engine.close()


@pytest.mark.asyncio
async def test_single_flight_pause_and_tick(tmp_path):
    class Slow(Adapter):
        async def lookup(self, watch):
            await asyncio.sleep(.05)
            return [offer()]

    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch())
    engine = Engine(db, {"synthetic": Slow()})
    first = asyncio.create_task(engine.check_watch(row["id"]))
    await asyncio.sleep(.01)
    assert (await engine.check_watch(row["id"]))["status"] == "busy"
    await first
    db.update_settings({"paused": True})
    assert (await engine.check_watch(row["id"]))["status"] == "paused"
    db.update_settings({"paused": False})
    db.force_due(row["id"], time.time() - 1)
    await engine.tick()
    assert len(db.history(db.list_offers(row["id"])[0]["id"])) == 2
    await engine.close()


@pytest.mark.asyncio
async def test_outbox_retry_and_discord_payload(tmp_path):
    class Client:
        def __init__(self):
            self.payloads = []
        async def post_json(self, url, payload, *, allowed_hosts=None):
            self.payloads.append((url, payload, allowed_hosts))

    client = Client()
    notifier = DiscordNotifier(client, "https://discord.com/api/webhooks/123/token")
    with pytest.raises(ValueError):
        DiscordNotifier(client, "https://example.org/api/webhooks/123/token")
    db = Database(tmp_path / "x.db")
    db.update_settings({"notification_enabled": True})
    row = db.create_watch(watch(target_cents=1500))
    engine = Engine(db, {"synthetic": Adapter([offer()])}, notifier)
    await engine.check_watch(row["id"])
    assert len(client.payloads) == 1
    assert client.payloads[0][1]["allowed_mentions"] == {"parse": []}
    await engine.close()


@pytest.mark.asyncio
async def test_older_source_observation_cannot_replace_current_or_alert(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(target_cents=1500))
    current = time.time()
    adapter = Adapter([offer(1000, checked_at=current)])
    engine = Engine(db, {"synthetic": adapter})
    await engine.check_watch(row["id"])
    adapter.results = [offer(800, checked_at=current - 10)]
    await engine.check_watch(row["id"])
    assert len(db.list_events()) == 1
    assert db.list_offers(row["id"])[0]["price_cents"] == 1000
    history = db.history(db.list_offers(row["id"])[0]["id"])
    assert [h["price_cents"] for h in history] == [800, 1000]
    await engine.close()


@pytest.mark.asyncio
async def test_notification_failure_is_retried_without_new_event(tmp_path):
    class Flaky:
        def __init__(self):
            self.calls = 0
        async def send(self, event):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic failure")

    db = Database(tmp_path / "x.db")
    db.update_settings({"notification_enabled": True})
    row = db.create_watch(watch(target_cents=1500))
    notifier = Flaky()
    engine = Engine(db, {"synthetic": Adapter([offer()])}, notifier)
    await engine.check_watch(row["id"])
    assert notifier.calls == 1 and len(db.list_events()) == 1
    db.conn.execute("UPDATE outbox SET next_due=0")
    await engine.tick()
    assert notifier.calls == 2 and len(db.list_events()) == 1
    await engine.close()


@pytest.mark.asyncio
async def test_discovery_passes_synonyms_to_adapter(tmp_path):
    class Search(Adapter):
        def __init__(self):
            self.requests = []
        async def search(self, watch):
            self.requests.append((watch.query, watch.synonyms))
            return [offer()]

    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(kind="discovery", query="Widget", synonyms=["Synthetic Widget"]))
    adapter = Search()
    engine = Engine(db, {"synthetic": adapter})
    result = await engine.check_watch(row["id"])
    assert result["status"] == "ok" and result["offers"] == 1
    assert adapter.requests == [("Widget", ["Synthetic Widget"])]
    assert len(db.history(db.list_offers(row["id"])[0]["id"])) == 1
    await engine.close()


@pytest.mark.asyncio
async def test_missing_source_reports_error_without_skipping(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(source_ids=["absent"]))
    engine = Engine(db, {})
    result = await engine.check_watch(row["id"])
    assert result["status"] == "error" and "absent" in result["error"]
    assert db.get_watch(row["id"])["failure_count"] == 1
    await engine.close()


@pytest.mark.asyncio
async def test_exact_title_ambiguity_is_visible(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch())
    second = offer().model_copy(update={"product_id": "s2"})
    engine = Engine(db, {"synthetic": Adapter([offer(), second])})
    result = await engine.check_watch(row["id"])
    assert result["status"] == "error" and "ambiguous" in result["error"]
    assert db.list_offers() == []
    await engine.close()


@pytest.mark.asyncio
async def test_edit_during_check_does_not_attach_old_offer(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    class Slow(Adapter):
        async def lookup(self, watch):
            started.set()
            await release.wait()
            return [offer()]

    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch())
    engine = Engine(db, {"synthetic": Slow()})
    task = asyncio.create_task(engine.check_watch(row["id"]))
    await started.wait()
    assert engine.is_running(row["id"])
    db.update_watch(row["id"], watch(query="Other Widget"))
    release.set()
    assert (await task)["status"] == "error"
    assert db.list_offers() == []
    await engine.close()


@pytest.mark.asyncio
async def test_scheduler_stop_drains_active_check(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    class Slow(Adapter):
        async def lookup(self, watch):
            started.set()
            await release.wait()
            return [offer()]

    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch())
    engine = Engine(db, {"synthetic": Slow()})
    scheduler = Scheduler(engine)
    await scheduler.start()
    await started.wait()
    stop_task = asyncio.create_task(scheduler.stop())
    await asyncio.sleep(.01)
    assert not stop_task.done()
    release.set()
    await stop_task
    assert db.list_offers(row["id"])
    await engine.close()


@pytest.mark.asyncio
async def test_outbox_delivery_is_single_flight(tmp_path):
    class SlowNotifier:
        def __init__(self):
            self.calls = 0
        async def send(self, event):
            self.calls += 1
            await asyncio.sleep(.02)

    db = Database(tmp_path / "x.db")
    db.update_settings({"notification_enabled": True})
    row = db.create_watch(watch(target_cents=1500))
    notifier = SlowNotifier()
    engine = Engine(db, {"synthetic": Adapter([offer()])})
    await engine.check_watch(row["id"])
    engine.notifier = notifier
    await asyncio.gather(engine.flush_outbox(), engine.flush_outbox())
    assert notifier.calls == 1
    await engine.close()


@pytest.mark.asyncio
async def test_further_alert_requires_drop_from_previous_observation(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(target_cents=1500))
    adapter = Adapter([offer(1000)])
    engine = Engine(db, {"synthetic": adapter})
    await engine.check_watch(row["id"])
    adapter.results = [offer(800)]
    await engine.check_watch(row["id"])
    adapter.results = [offer(900)]
    await engine.check_watch(row["id"])
    adapter.results = [offer(850)]
    await engine.check_watch(row["id"])
    assert len(db.list_events()) == 2
    await engine.close()


@pytest.mark.asyncio
async def test_exact_url_uses_adapter_resolution(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(query="", url="https://apps.apple.com/de/app/old-name/id123"))
    item = offer().model_copy(update={"url": "https://apps.apple.com/de/app/new-name/id123"})
    engine = Engine(db, {"synthetic": Adapter([item])})
    result = await engine.check_watch(row["id"])
    assert result["offers"] == 1
    await engine.close()


@pytest.mark.asyncio
async def test_discord_payload_includes_observed_context():
    class Client:
        async def post_json(self, url, payload, *, allowed_hosts=None):
            self.payload = payload
    client = Client()
    notifier = DiscordNotifier(client, "https://discord.com/api/webhooks/1/token")
    await notifier.send({"reason": "Neuer Bestpreis", "product": "Synthetic Widget", "merchant": "Synthetic Shop",
                         "total_cents": 900, "comparison_cents": 1200, "savings_cents": 300,
                         "url": "https://example.org/item", "checked_at": 1_700_000_000,
                         "availability": "in_stock"})
    content = client.payload["content"]
    assert "12,00" in content and "3,00" in content
    assert "2023" in content and "in_stock" in content


def test_outbox_status_and_exponential_backoff(tmp_path):
    db = Database(tmp_path / "x.db")
    db.update_settings({"notification_enabled": True})
    row = db.create_watch(watch(target_cents=1500))
    db.record_check(row["id"], [(offer(), "Wunschpreis erreicht", None, None, "Wunschpreis erreicht")], "ok", None, time.time())
    item = db.due_outbox()[0]
    db.outbox_result(item["id"], "synthetic failure")
    first = db.conn.execute("SELECT next_due FROM outbox").fetchone()[0]
    db.outbox_result(item["id"], "synthetic failure")
    second = db.conn.execute("SELECT next_due FROM outbox").fetchone()[0]
    assert second - time.time() > first - time.time() + 50
    event = db.list_events()[0]
    assert event["notification_status"] == "pending"
    assert event["notification_attempts"] == 2
    db.outbox_result(item["id"], None)
    assert db.list_events()[0]["notification_status"] == "sent"


def test_black_friday_advances_existing_due(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch(priority="normal"))
    checked = time.time() - 1000
    db.record_check(row["id"], [], "ok", None, checked)
    old_due = db.get_watch(row["id"])["next_due"]
    db.update_settings({"black_friday_until": time.time() + 3600})
    assert db.get_watch(row["id"])["next_due"] < old_due
    assert db.get_watch(row["id"])["next_due"] <= time.time()


@pytest.mark.asyncio
async def test_due_offer_is_marked_stale(tmp_path):
    db = Database(tmp_path / "x.db")
    row = db.create_watch(watch())
    engine = Engine(db, {"synthetic": Adapter([offer()])})
    await engine.check_watch(row["id"])
    assert db.list_offers(row["id"])[0]["stale"] is False
    db.force_due(row["id"], time.time() - 1)
    assert db.list_offers(row["id"])[0]["stale"] is True
    await engine.close()


@pytest.mark.asyncio
async def test_scheduler_survives_tick_error():
    class FlakyEngine:
        def __init__(self):
            self.calls = 0
        async def tick(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic error")
    engine = FlakyEngine()
    scheduler = Scheduler(engine)
    await scheduler.start()
    await asyncio.sleep(.01)
    assert scheduler._task is not None and not scheduler._task.done()
    await scheduler.stop()
