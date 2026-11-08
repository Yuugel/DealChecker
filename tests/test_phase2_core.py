"""Synthetic phase-2 contracts; no merchant requests."""
import time
import json
import asyncio
import sqlite3

import pytest
from pydantic import ValidationError

from dealchecker.db import Database, interval
from dealchecker.adapters.feed import FeedAdapter
from dealchecker.models import Offer, WatchInput
from dealchecker.service import Engine


def offer(source="a", merchant="Shop A", price=1000, **changes):
    data = dict(source=source, merchant=merchant, product_id="p1", name="Synthetic Widget",
                url="https://example.org/p1", price_cents=price, shipping_cents=100,
                availability="in_stock", checked_at=time.time())
    return Offer(**(data | changes))


def watch(**changes):
    return WatchInput(**(dict(name="Synthetic Widget", kind="discovery", query="Synthetic Widget",
                              source_ids=["a", "b"], target_cents=2000) | changes))


class Adapter:
    min_interval_seconds = 300

    def __init__(self, results):
        self.results = results

    async def search(self, watch):
        return self.results

    lookup = search


def test_identity_defaults_preserve_legacy_key_but_explicit_seller_pack_condition_split():
    base = offer()
    assert base.key == '["a","Shop A","p1",""]'
    assert base.model_dump()["offer_type"] == "retail"
    assert base.model_copy(update={"seller": "Seller One"}).key != base.key
    assert base.model_copy(update={"pack_size": "2 pack"}).key != base.key
    assert base.model_copy(update={"condition": "used"}).key != base.key
    assert base.model_copy(update={"offer_type": "reference"}).key != base.key
    with pytest.raises(ValidationError):
        offer(condition="broken")
    with pytest.raises(ValidationError):
        offer(offer_type="reference", shipping_cents=0)


def test_interval_override_wire_shape_and_selected_source_validation():
    value = watch(black_friday_interval_seconds=600,
                  source_intervals={"a": {"normal_seconds": 3600, "black_friday_seconds": 900}})
    assert value.model_dump()["source_intervals"]["a"] == {"normal_seconds": 3600, "black_friday_seconds": 900}
    with pytest.raises(ValidationError):
        watch(source_intervals={"absent": {"normal_seconds": 3600, "black_friday_seconds": 900}})
    with pytest.raises(ValidationError):
        watch(source_intervals={"a": {"normal_seconds": 299, "black_friday_seconds": 900}})


def test_adaptive_cadence_and_source_precedence(monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    normal = {"black_friday_until": 0}
    week = {"black_friday_until": clock[0] + 3600}
    assert interval(watch(kind="discovery", priority="critical").model_dump(), normal) == 28800
    assert interval(watch(kind="discovery", priority="critical").model_dump(), week) == 10800
    assert interval(watch(kind="exact", priority="normal").model_dump(), normal) == 7200
    assert interval(watch(kind="exact", priority="normal").model_dump(), week) == 1800
    assert interval(watch(kind="exact", priority="high").model_dump(), week) == 600
    assert interval(watch(kind="exact", priority="critical").model_dump(), week) == 300
    assert interval(watch(kind="exact", priority="low").model_dump(), week) == 3600
    custom = watch(source_intervals={"a": {"normal_seconds": 5400, "black_friday_seconds": 600}},
                   interval_seconds=9000, black_friday_interval_seconds=1200).model_dump()
    assert interval(custom, normal, source_id="a", minimum=6000) == 6000
    assert interval(custom, week, source_id="a", minimum=300) == 600
    assert interval(custom, week, source_id="b", minimum=300) == 1200


def test_legacy_watch_cadence_migrates_without_replacing_due(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(path)
    row = db.create_watch(watch(kind="exact", source_ids=["a"], priority="normal"))
    old_due = db.get_watch(row["id"])["next_due"] + 1000
    with db.conn:
        data = json.loads(db.conn.execute("SELECT data FROM watches WHERE id=?", (row["id"],)).fetchone()[0])
        data.pop("black_friday_interval_seconds", None)
        data.pop("source_intervals", None)
        db.conn.execute("UPDATE watches SET data=?,next_due=? WHERE id=?", (json.dumps(data), old_due, row["id"]))
        db.conn.execute("UPDATE watch_sources SET next_due=? WHERE watch_id=?", (old_due, row["id"]))
        db.conn.execute("ALTER TABLE watch_sources DROP COLUMN pending_refresh")
        db.conn.execute("ALTER TABLE watch_sources DROP COLUMN min_interval_seconds")
        db.conn.execute("PRAGMA user_version=4")
    db.close()
    reopened = Database(path)
    migrated = reopened.get_watch(row["id"])
    assert reopened.schema_version == 6
    assert migrated["interval_seconds"] == 1800
    assert migrated["black_friday_interval_seconds"] == 900
    assert migrated["next_due"] == old_due
    reopened.close()


def test_schema_five_migration_rolls_back_when_copy_fails(tmp_path, monkeypatch):
    path = tmp_path / "interrupted-v4.db"
    db = Database(path)
    db.create_watch(watch(kind="exact", source_ids=["a"]))
    with db.conn:
        db.conn.execute("ALTER TABLE watch_sources DROP COLUMN pending_refresh")
        db.conn.execute("ALTER TABLE watch_sources DROP COLUMN min_interval_seconds")
        db.conn.execute("PRAGMA user_version=4")
    db.close()
    import dealchecker.db as db_module
    with monkeypatch.context() as patch:
        patch.setattr(db_module.json, "loads", lambda value: (_ for _ in ()).throw(RuntimeError("interrupted")))
        with pytest.raises(RuntimeError, match="interrupted"):
            Database(path)
    with sqlite3.connect(path) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
        assert "min_interval_seconds" not in {column[1] for column in raw.execute("PRAGMA table_info(watch_sources)")}
    reopened = Database(path)
    assert reopened.schema_version == 6
    reopened.close()


def test_black_week_reschedules_healthy_with_source_floor_without_accelerating_failure(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "week.db")
    row = db.create_watch(watch(kind="exact", source_ids=["a", "b"]))
    db.record_check(row["id"], [], "error", "b: fixture failure", clock[0],
                    source_outcomes={"a": (None, 3600), "b": ("fixture failure", 0)})
    before = db.source_schedules(row["id"])
    assert before["a"]["next_due"] == clock[0] + 7200
    assert before["b"]["next_due"] == clock[0] + 14400
    db.update_settings({"black_friday_until": clock[0] + 7 * 86400})
    after = db.source_schedules(row["id"])
    assert after["a"]["next_due"] == clock[0] + 3600
    assert after["b"]["next_due"] == before["b"]["next_due"]
    assert after["a"]["min_interval_seconds"] == 3600
    db.close()


def test_black_week_expiry_restores_normal_due_without_early_retry(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "expiry.db")
    row = db.create_watch(watch(kind="exact", source_ids=["a"]))
    db.update_settings({"black_friday_until": clock[0] + 600})
    db.record_check(row["id"], [], "ok", None, clock[0])
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 1800
    clock[0] += 601
    assert db.due_watches() == []
    assert db.get_watch(row["id"])["next_due"] == 1_800_000_000.0 + 7200
    assert db.get_settings()["black_friday_until"] == 0
    db.close()


def test_black_week_expiry_keeps_failure_backoff_and_error(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "failed-expiry.db")
    row = db.create_watch(watch(kind="exact", source_ids=["a"]))
    db.update_settings({"black_friday_until": clock[0] + 600})
    db.record_check(row["id"], [], "error", "a: unavailable", clock[0],
                    source_outcomes={"a": ("unavailable", 0)})
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 3600
    clock[0] += 601
    assert db.due_watches() == []
    assert db.get_watch(row["id"])["next_due"] == 1_800_000_000.0 + 14400
    assert db.source_schedules(row["id"])["a"]["failure_count"] == 1
    assert "unavailable" in db.get_watch(row["id"])["last_error"]
    db.close()


def test_edited_query_due_survives_black_week_expiry(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "edit-expiry.db")
    original = watch(kind="exact", source_ids=["a"], query="Old")
    row = db.create_watch(original)
    db.update_settings({"black_friday_until": clock[0] + 600})
    db.record_check(row["id"], [], "ok", None, clock[0])
    clock[0] += 500
    db.update_watch(row["id"], original.model_copy(update={"query": "New"}))
    assert db.get_watch(row["id"])["next_due"] == clock[0]
    clock[0] += 101
    assert [due["id"] for due in db.due_watches()] == [row["id"]]
    assert db.get_watch(row["id"])["next_due"] == 1_800_000_500.0
    db.record_check(row["id"], [], "ok", None, clock[0])
    assert db.source_schedules(row["id"])["a"]["pending_refresh"] == 0
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 7200
    db.close()


def test_interval_edit_recalculates_due_without_clearing_failure(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "edit.db")
    original = watch(kind="exact")
    row = db.create_watch(original)
    db.record_check(row["id"], [], "error", "b: failure", clock[0],
                    source_outcomes={"a": (None, 0), "b": ("failure", 0)})
    db.update_watch(row["id"], original.model_copy(update={"interval_seconds": 3600}))
    after = db.source_schedules(row["id"])
    assert after["a"]["next_due"] == clock[0] + 3600
    assert after["b"]["next_due"] == clock[0] + 14400
    assert after["b"]["failure_count"] == 1
    db.close()


@pytest.mark.asyncio
async def test_adapter_minimum_change_on_restart_updates_persisted_due(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    path = tmp_path / "new-minimum.db"
    db = Database(path)
    row = db.create_watch(watch(kind="exact", source_ids=["a"], interval_seconds=300))
    db.record_check(row["id"], [], "ok", None, clock[0], source_outcomes={"a": (None, 300)})
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 300
    db.close()
    db = Database(path)
    class Slower(Adapter):
        min_interval_seconds = 3600
    engine = Engine(db, {"a": Slower([])})
    assert db.source_schedules(row["id"])["a"]["min_interval_seconds"] == 3600
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 3600
    await engine.close()
    db.close()


@pytest.mark.asyncio
async def test_reference_price_never_creates_purchase_alarm(tmp_path):
    db = Database(tmp_path / "reference.db")
    row = db.create_watch(watch(source_ids=["a"]))
    ref = offer(offer_type="reference", shipping_cents=None, price_cents=500)
    engine = Engine(db, {"a": Adapter([ref])})
    result = await engine.check_watch(row["id"])
    assert result["status"] == "ok" and result["offers"] == 1
    assert db.list_events() == []
    stored = db.list_offers(row["id"])[0]
    assert stored["offer_type"] == "reference"
    assert stored["evaluation"] == "Referenzpreis"
    await engine.close()
    db.close()


@pytest.mark.asyncio
async def test_current_offer_freshness_uses_source_override(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "freshness.db")
    row = db.create_watch(watch(kind="exact", source_ids=["a"],
                                source_intervals={"a": {"normal_seconds": 300, "black_friday_seconds": 300}}))
    adapter = Adapter([offer(checked_at=clock[0] - 700)])
    engine = Engine(db, {"a": adapter})
    await engine.check_watch(row["id"])
    assert db.list_events() == []
    assert db.list_offers(row["id"])[0]["evaluation"] == "Veraltete Daten"
    await engine.close()
    db.close()


@pytest.mark.asyncio
async def test_black_week_expiry_during_fetch_keeps_valid_observation(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    started, release = asyncio.Event(), asyncio.Event()

    class Slow:
        min_interval_seconds = 300

        async def search(self, watch):
            started.set()
            await release.wait()
            return [offer(checked_at=clock[0])]

        lookup = search

    db = Database(tmp_path / "cross-expiry.db")
    row = db.create_watch(watch(source_ids=["a"]))
    db.update_settings({"black_friday_until": clock[0] + 600})
    db.record_check(row["id"], [], "ok", None, clock[0])
    engine = Engine(db, {"a": Slow()})
    pending = asyncio.create_task(engine.check_watch(row["id"]))
    await started.wait()
    clock[0] += 601
    release.set()
    result = await pending
    assert result["status"] == "ok" and result["offers"] == 1
    assert db.list_offers(row["id"])[0]["price_cents"] == 1000
    await engine.close()
    db.close()


def test_comparisons_require_valid_gtin_or_strict_model_and_compatible_details(tmp_path):
    db = Database(tmp_path / "compare.db")
    row = db.create_watch(watch())
    a = offer(gtin="4006381333931", manufacturer="Acme", model="M1", variant="red", pack_size="1",
              condition="new")
    b = offer(source="b", merchant="Shop B", price=900, gtin="4006381333931", manufacturer="Acme",
              model="M1", variant="red", pack_size="1", condition="new")
    db.record_check(row["id"], [(a, "x", None, None, None), (b, "x", None, None, None)], "ok", None, time.time())
    groups = db.comparisons()
    assert len(groups) == 1
    assert groups[0]["identity_basis"] == "gtin"
    assert groups[0]["identity_value"] == "04006381333931"
    assert groups[0]["lowest_total_cents"] == 1000
    assert len(groups[0]["offer_ids"]) == 2
    db.close()


def test_comparisons_do_not_merge_invalid_gtin_name_or_incompatible_pack(tmp_path):
    db = Database(tmp_path / "no-compare.db")
    row = db.create_watch(watch())
    a = offer(gtin="4006381333931", manufacturer="Acme", model="M1", variant="red", pack_size="1")
    b = offer(source="b", merchant="Shop B", gtin="4006381333931", manufacturer="Acme",
              model="M1", variant="red", pack_size="2")
    db.record_check(row["id"], [(a, "x", None, None, None), (b, "x", None, None, None)], "ok", None, time.time())
    assert db.comparisons() == []
    db.close()

    db = Database(tmp_path / "stale-compare.db")
    row = db.create_watch(watch())
    a = offer(gtin="4006381333931")
    b = offer(source="b", merchant="Shop B", gtin="4006381333931", checked_at=time.time() - 100000)
    db.record_check(row["id"], [(a, "x", None, None, None), (b, "x", None, None, None)], "ok", None, time.time())
    assert db.comparisons() == []
    db.close()

    db = Database(tmp_path / "invalid-gtin.db")
    row = db.create_watch(watch())
    a = offer(gtin="4006381333932", manufacturer="Acme", model="M1", variant="red", pack_size="1")
    b = offer(source="b", merchant="Shop B", gtin="4006381333932", manufacturer="Acme",
              model="M1", variant="red", pack_size="1")
    db.record_check(row["id"], [(a, "x", None, None, None), (b, "x", None, None, None)], "ok", None, time.time())
    assert db.comparisons() == []
    db.close()


def test_comparisons_exclude_condition_mismatch_unknown_shipping_and_stale(tmp_path):
    db = Database(tmp_path / "unsafe.db")
    row = db.create_watch(watch())
    a = offer(gtin="4006381333931", condition="new")
    b = offer(source="b", merchant="Shop B", gtin="4006381333931", condition="used")
    db.record_check(row["id"], [(a, "x", None, None, None), (b, "x", None, None, None)], "ok", None, time.time())
    assert db.comparisons() == []
    db.close()

    db = Database(tmp_path / "unknown-shipping.db")
    row = db.create_watch(watch())
    a = offer(gtin="4006381333931")
    b = offer(source="b", merchant="Shop B", gtin="4006381333931", shipping_cents=None)
    db.record_check(row["id"], [(a, "x", None, None, None), (b, "x", None, None, None)], "ok", None, time.time())
    assert db.comparisons() == []
    db.close()


def test_model_fallback_requires_explicit_variant_and_pack_and_excludes_reference(tmp_path):
    db = Database(tmp_path / "fallback.db")
    row = db.create_watch(watch())
    a = offer(manufacturer="Acme", model="M1", variant="red", pack_size="1")
    b = offer(source="b", merchant="Shop B", manufacturer="Acme", model="M1", variant="red", pack_size="1")
    ref = offer(source="b", merchant="Price index", product_id="ref", manufacturer="Acme", model="M1",
                variant="red", pack_size="1", offer_type="reference", shipping_cents=None)
    db.record_check(row["id"], [(item, "x", None, None, None) for item in (a, b, ref)], "ok", None, time.time())
    groups = db.comparisons()
    assert len(groups) == 1 and groups[0]["identity_basis"] == "manufacturer_model"
    assert len(groups[0]["offer_ids"]) == 2
    db.close()


@pytest.mark.asyncio
async def test_authorized_feed_preserves_explicit_seller_pack_condition_and_reference():
    class Client:
        async def get_json(self, url, *, allowed_hosts):
            base = dict(product_id="p1", name="Synthetic Widget", url="https://example.org/p1",
                        price_cents=1000, shipping_cents=100, currency="EUR", availability="in_stock",
                        merchant="Market", variant="red")
            return {"offers": [base | {"seller": "Seller A", "pack_size": "1", "condition": "new"},
                               base | {"seller": "Seller B", "pack_size": "2", "condition": "used"},
                               base | {"seller": "Index", "offer_type": "reference", "shipping_cents": None}]}
    adapter = FeedAdapter(Client(), {"id": "fixture", "name": "Fixture", "url": "https://example.org/feed.json"})
    results = await adapter.search(watch(source_ids=["fixture"]))
    assert len(results) == 3
    assert {result.seller for result in results} == {"Seller A", "Seller B", "Index"}
    assert len({result.key for result in results}) == 3
    assert next(result for result in results if result.offer_type == "reference").total_cents is None
