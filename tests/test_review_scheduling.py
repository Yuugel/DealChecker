"""Synthetic source scheduling regressions; no merchant traffic."""
import asyncio
import sqlite3
import time

import pytest

from dealchecker.db import Database
from dealchecker.models import Offer, WatchInput
from dealchecker.service import Engine


def item(source, price, when):
    return Offer(source=source, merchant="Fixture", product_id="widget", name="Synthetic Widget",
                 url="https://example.org/widget", price_cents=price, shipping_cents=0,
                 availability="in_stock", checked_at=when)


class Source:
    def __init__(self, name, clock, price=3000, failing=False):
        self.name, self.clock, self.price, self.failing = name, clock, price, failing
        self.calls = 0

    async def search(self, watch):
        self.calls += 1
        if self.failing:
            raise RuntimeError("fixture failure")
        return [item(self.name, self.price, self.clock[0])]

    lookup = search


def test_schema_three_upgrade_preserves_existing_watch_due(tmp_path):
    path = tmp_path / "old.db"
    db = Database(path)
    row = db.create_watch(WatchInput(name="Synthetic", kind="discovery", query="Synthetic",
                                     source_ids=["a", "b"]))
    db.force_due(row["id"], 1_800_000_123.0)
    with db.conn:
        db.conn.execute("DROP TABLE watch_sources")
        db.conn.execute("PRAGMA user_version=3")
    db.close()
    db = Database(path)
    assert db.schema_version == 4
    assert {source: state["next_due"] for source, state in db.source_schedules(row["id"]).items()} == {
        "a": 1_800_000_123.0, "b": 1_800_000_123.0}
    assert db.get_watch(row["id"])["next_due"] == 1_800_000_123.0
    db.close()


def test_schema_four_migration_rolls_back_on_interrupted_copy(tmp_path, monkeypatch):
    path = tmp_path / "interrupted.db"
    db = Database(path)
    db.create_watch(WatchInput(name="Synthetic", kind="discovery", query="Synthetic", source_ids=["a"]))
    with db.conn:
        db.conn.execute("DROP TABLE watch_sources")
        db.conn.execute("PRAGMA user_version=3")
    db.close()

    import dealchecker.db as db_module
    with monkeypatch.context() as patch:
        patch.setattr(db_module.json, "loads", lambda value: (_ for _ in ()).throw(RuntimeError("interrupted")))
        with pytest.raises(RuntimeError, match="interrupted"):
            Database(path)
    with sqlite3.connect(path) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 3
        assert raw.execute("SELECT name FROM sqlite_master WHERE name='watch_sources'").fetchone() is None
    reopened = Database(path)
    assert reopened.schema_version == 4
    reopened.close()


def test_schema_three_partial_failure_does_not_inherit_global_backoff(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    path = tmp_path / "partial-v3.db"
    db = Database(path)
    row = db.create_watch(WatchInput(name="Synthetic", kind="discovery", query="Synthetic",
                                     source_ids=["good", "bad"], priority="critical"))
    with db.conn:
        db.conn.execute("UPDATE watches SET next_due=?,failure_count=4,last_error=? WHERE id=?",
                        (clock[0] + 4800, "bad: unavailable", row["id"]))
        db.conn.execute("DROP TABLE watch_sources")
        db.conn.execute("PRAGMA user_version=3")
    db.close()
    db = Database(path)
    assert db.schema_version == 4
    assert db.get_watch(row["id"])["next_due"] <= clock[0]
    assert all(state["failure_count"] == 0 and state["next_due"] <= clock[0]
               for state in db.source_schedules(row["id"]).values())
    db.close()


@pytest.mark.asyncio
async def test_healthy_source_keeps_normal_cadence_while_failure_backs_off_across_restart(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    path = tmp_path / "schedule.db"
    db = Database(path)
    row = db.create_watch(WatchInput(name="Synthetic", kind="discovery", query="Synthetic",
                                     source_ids=["bad", "good"], priority="critical"))
    bad, good = Source("bad", clock, failing=True), Source("good", clock)
    engine = Engine(db, {"bad": bad, "good": good})
    await engine.tick()
    assert (bad.calls, good.calls) == (1, 1)
    assert db.get_watch(row["id"])["last_error"] is not None
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 300
    assert db.list_offers(row["id"])[0]["stale"] is False
    await engine.close()
    db.close()

    clock[0] += 300
    db = Database(path)
    good.price = 2500
    engine = Engine(db, {"bad": bad, "good": good})
    await engine.tick()
    assert (bad.calls, good.calls) == (1, 2)
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 300
    assert [event["total_cents"] for event in db.list_events()] == [2500]
    clock[0] += 300
    good.price = 2000
    await engine.tick()
    assert (bad.calls, good.calls) == (2, 3)
    assert [event["total_cents"] for event in db.list_events()] == [2000, 2500]
    clock[0] += 300
    await engine.tick()
    assert (bad.calls, good.calls) == (2, 4)
    assert [event["total_cents"] for event in db.list_events()] == [2000, 2500]
    await engine.check_watch(row["id"])
    assert bad.calls == 2
    await engine.close()
    db.close()


@pytest.mark.asyncio
async def test_hung_first_source_does_not_prevent_later_source_result(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "hung.db")
    row = db.create_watch(WatchInput(name="Synthetic", kind="discovery", query="Synthetic",
                                     source_ids=["hung", "good"], priority="critical"))

    class Hung(Source):
        async def search(self, watch):
            await asyncio.sleep(3600)

    good = Source("good", clock)
    engine = Engine(db, {"hung": Hung("hung", clock), "good": good})
    monkeypatch.setattr("dealchecker.service.SOURCE_CHECK_TIMEOUT", .03)
    result = await asyncio.wait_for(engine.check_watch(row["id"]), timeout=1)
    assert good.calls == 1
    assert result["status"] == "error" and result["offers"] == 1
    assert "hung: check deadline exceeded" in result["error"]
    assert db.list_offers(row["id"])[0]["source"] == "good"
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 300
    await engine.close()
    db.close()


@pytest.mark.asyncio
async def test_skipped_failed_source_does_not_stale_healthy_or_retry_early(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "fresh.db")
    row = db.create_watch(WatchInput(name="Synthetic", kind="discovery", query="Synthetic",
                                     source_ids=["good", "bad"], priority="critical"))
    good, bad = Source("good", clock), Source("bad", clock)
    engine = Engine(db, {"good": good, "bad": bad})
    await engine.tick()
    clock[0] += 300
    bad.failing = True
    await engine.tick()
    assert {offer["source"]: offer["stale"] for offer in db.list_offers(row["id"])} == {"good": False, "bad": True}
    clock[0] += 300
    good.price = 2500
    await engine.tick()
    assert (good.calls, bad.calls) == (3, 2)
    offers = {offer["source"]: offer for offer in db.list_offers(row["id"])}
    assert offers["good"]["stale"] is False
    assert offers["bad"]["stale"] is True
    assert "bad: fixture failure" in db.get_watch(row["id"])["last_error"]
    assert db.list_checks()[0]["status"] == "error"
    await engine.close()
    db.close()


@pytest.mark.asyncio
async def test_pause_edit_and_black_friday_keep_source_schedule_coherent(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = Database(tmp_path / "settings.db")
    initial = WatchInput(name="Synthetic", kind="discovery", query="Synthetic",
                         source_ids=["good", "bad"], priority="normal")
    row = db.create_watch(initial)
    good, bad = Source("good", clock), Source("bad", clock, failing=True)
    engine = Engine(db, {"good": good, "bad": bad})
    await engine.tick()
    assert db.get_watch(row["id"])["next_due"] == clock[0] + 1800
    db.update_settings({"paused": True})
    clock[0] += 900
    await engine.tick()
    assert (good.calls, bad.calls) == (1, 1)
    db.update_settings({"black_friday_until": clock[0] + 3600})
    assert db.get_watch(row["id"])["next_due"] == clock[0]
    db.update_settings({"paused": False})
    await engine.tick()
    assert (good.calls, bad.calls) == (2, 1)
    paused_edit = initial.model_copy(update={"paused": True})
    db.update_watch(row["id"], paused_edit)
    assert db.get_watch(row["id"])["last_error"] is not None
    db.update_watch(row["id"], initial)
    await engine.tick()
    assert bad.calls == 1
    edited = initial.model_copy(update={"source_ids": ["good", "new"]})
    db.update_watch(row["id"], edited)
    assert set(db.source_schedules(row["id"])) == {"good", "new"}
    assert db.get_watch(row["id"])["last_error"] is None
    await engine.close()
    db.close()
