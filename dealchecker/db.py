"""SQLite state. Every completed check is committed in one transaction."""
import json
import sqlite3
import time
from pathlib import Path

from .models import Offer, WatchInput
from .identity import comparison_identity
from .scheduling import OLD_DEFAULTS, interval, interval_pair

SCHEMA_VERSION = 5
FEED_FRESH_SECONDS = 600


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        try:
            self._migrate()
        except Exception:
            self.conn.close()
            raise

    def _migrate(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("database schema is newer than this application")
        if version < 1:
            self.conn.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE watches (id INTEGER PRIMARY KEY, data TEXT NOT NULL,
                    next_due REAL NOT NULL, failure_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT, last_checked REAL);
                CREATE TABLE offers (id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                    offer_key TEXT NOT NULL, data TEXT NOT NULL, evaluation TEXT NOT NULL,
                    comparison_cents INTEGER, savings_cents INTEGER, observed_at REAL NOT NULL,
                    UNIQUE(watch_id, offer_key));
                CREATE TABLE observations (id INTEGER PRIMARY KEY, offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
                    checked_at REAL NOT NULL, price_cents INTEGER NOT NULL, shipping_cents INTEGER,
                    total_cents INTEGER, availability TEXT NOT NULL, provenance TEXT NOT NULL);
                CREATE INDEX observations_offer ON observations(offer_id,id);
                CREATE TABLE events (id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                    offer_key TEXT NOT NULL, total_cents INTEGER NOT NULL, data TEXT NOT NULL,
                    created_at REAL NOT NULL, last_alerted_total INTEGER NOT NULL,
                    UNIQUE(watch_id,offer_key,total_cents));
                CREATE TABLE checks (id INTEGER PRIMARY KEY, watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                    checked_at REAL NOT NULL, status TEXT NOT NULL, offers INTEGER NOT NULL, error TEXT);
                CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0,
                    black_friday_until REAL NOT NULL DEFAULT 0, notification_enabled INTEGER NOT NULL DEFAULT 0);
                INSERT INTO settings(id) VALUES(1);
                CREATE TABLE outbox (id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_due REAL NOT NULL, last_error TEXT);
                PRAGMA user_version=1;
                COMMIT;
                """)
            version = 1
        if version < 2:
            self.conn.executescript("""
                BEGIN IMMEDIATE;
                DELETE FROM observations WHERE id NOT IN (
                    SELECT MIN(id) FROM observations GROUP BY offer_id, checked_at
                );
                CREATE UNIQUE INDEX observations_unique_time ON observations(offer_id,checked_at);
                PRAGMA user_version=2;
                COMMIT;
                """)
            version = 2
        if version < 3:
            self.conn.executescript("""
                BEGIN IMMEDIATE;
                ALTER TABLE offers ADD COLUMN seen_at REAL NOT NULL DEFAULT 0;
                CREATE TABLE source_status (
                    source_id TEXT PRIMARY KEY,
                    last_checked REAL NOT NULL,
                    last_success REAL,
                    last_error TEXT
                );
                PRAGMA user_version=3;
                COMMIT;
                """)
            version = 3
        if version < 4:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("""CREATE TABLE watch_sources (
                    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL, next_due REAL NOT NULL,
                    failure_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                    last_checked REAL, PRIMARY KEY(watch_id, source_id))""")
                for row in self.conn.execute("SELECT * FROM watches"):
                    # The v3 failure belongs to an unknown source. Recheck all
                    # sources promptly instead of assigning its backoff to each.
                    failed = row["failure_count"] > 0
                    due = min(row["next_due"], time.time()) if failed else row["next_due"]
                    for source in json.loads(row["data"])["source_ids"]:
                        self.conn.execute("""INSERT INTO watch_sources
                            (watch_id,source_id,next_due,failure_count,last_error,last_checked)
                            VALUES(?,?,?,?,?,?)""", (row["id"], source, due, 0, None,
                                               None if failed else row["last_checked"]))
                    if failed:
                        self.conn.execute("UPDATE watches SET next_due=? WHERE id=?", (due, row["id"]))
                self.conn.execute("PRAGMA user_version=4")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            version = 4
        if version < 5:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("ALTER TABLE watch_sources ADD COLUMN min_interval_seconds INTEGER NOT NULL DEFAULT 0")
                for row in self.conn.execute("SELECT id,data FROM watches"):
                    data = json.loads(row["data"])
                    old_normal = data.get("interval_seconds") or OLD_DEFAULTS[data["priority"]]
                    data["interval_seconds"] = old_normal
                    data.setdefault("black_friday_interval_seconds", max(300, old_normal // 2))
                    data.setdefault("source_intervals", {})
                    self.conn.execute("UPDATE watches SET data=? WHERE id=?", (json.dumps(data), row["id"]))
                self.conn.execute("PRAGMA user_version=5")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    @property
    def schema_version(self):
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _watch(self, row):
        if row is None:
            return None
        return {**json.loads(row["data"]), "id": row["id"], "next_due": row["next_due"],
                "failure_count": row["failure_count"], "last_error": row["last_error"],
                "last_checked": row["last_checked"], "running": False}

    def create_watch(self, data: WatchInput) -> dict:
        now = time.time()
        with self.conn:
            cur = self.conn.execute("INSERT INTO watches(data,next_due) VALUES(?,?)",
                                    (data.model_dump_json(), now))
            self.conn.executemany("INSERT INTO watch_sources(watch_id,source_id,next_due) VALUES(?,?,?)",
                                  [(cur.lastrowid, source, now) for source in data.source_ids])
        return self.get_watch(cur.lastrowid)

    def update_watch(self, id: int, data: WatchInput) -> dict | None:
        old_watch = self.get_watch(id)
        if old_watch is None:
            return None
        now = time.time()
        schedule_fields = {"interval_seconds", "black_friday_interval_seconds", "source_intervals", "priority", "paused"}
        old_data = {k: old_watch[k] for k in WatchInput.model_fields}
        new_data = data.model_dump()
        changed_query = any(old_data[k] != new_data[k] for k in old_data if k not in schedule_fields)
        settings = self.get_settings()
        with self.conn:
            cur = self.conn.execute("UPDATE watches SET data=?,next_due=? WHERE id=?",
                                    (data.model_dump_json(), now, id))
            if cur.rowcount:
                old = self.source_schedules(id)
                self.conn.execute("DELETE FROM watch_sources WHERE watch_id=?", (id,))
                for source in data.source_ids:
                    state = old.get(source)
                    if state is None:
                        values = (id, source, now, 0, None, None, 0)
                    else:
                        minimum = state["min_interval_seconds"]
                        candidate = (state["last_checked"] + interval(new_data, settings, source, minimum, now) *
                                     min(2 ** state["failure_count"], 16)) if state["last_checked"] is not None else state["next_due"]
                        due = max(state["next_due"], candidate) if state["failure_count"] else (now if changed_query else candidate)
                        values = (id, source, due, state["failure_count"], state["last_error"],
                                  state["last_checked"], minimum)
                    self.conn.execute("""INSERT INTO watch_sources
                        (watch_id,source_id,next_due,failure_count,last_error,last_checked,min_interval_seconds)
                        VALUES(?,?,?,?,?,?,?)""", values)
                current = self.source_schedules(id)
                errors = [f"{source}: {state['last_error']}" for source, state in current.items() if state["last_error"]]
                self.conn.execute("UPDATE watches SET next_due=?,failure_count=?,last_error=? WHERE id=?",
                                  (min(s["next_due"] for s in current.values()),
                                   max(s["failure_count"] for s in current.values()), "; ".join(errors) or None, id))
        return self.get_watch(id) if cur.rowcount else None

    def delete_watch(self, id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM watches WHERE id=?", (id,))
        return bool(cur.rowcount)

    def get_watch(self, id: int) -> dict | None:
        self._expire_black_week()
        return self._watch(self.conn.execute("SELECT * FROM watches WHERE id=?", (id,)).fetchone())

    def list_watches(self) -> list[dict]:
        self._expire_black_week()
        return [self._watch(r) for r in self.conn.execute("SELECT * FROM watches ORDER BY id DESC")]

    def force_due(self, id: int, when: float):
        with self.conn:
            self.conn.execute("UPDATE watches SET next_due=? WHERE id=?", (when, id))
            self.conn.execute("UPDATE watch_sources SET next_due=? WHERE watch_id=?", (when, id))

    def source_schedules(self, id: int) -> dict[str, dict]:
        return {r["source_id"]: dict(r) for r in self.conn.execute(
            "SELECT * FROM watch_sources WHERE watch_id=?", (id,))}

    def effective_source_intervals(self, id: int, adapter_minimums: dict[str, int] | None = None) -> dict[str, dict]:
        watch = self.get_watch(id)
        if watch is None:
            return {}
        settings = self.get_settings()
        adapter_minimums = adapter_minimums or {}
        result = {}
        for source, state in self.source_schedules(id).items():
            minimum = max(state["min_interval_seconds"], adapter_minimums.get(source, 0))
            normal, week = interval_pair(watch, source, minimum)
            result[source] = {"normal_seconds": normal, "black_friday_seconds": week,
                              "effective_seconds": interval(watch, settings, source, minimum),
                              "min_interval_seconds": minimum, "next_due": state["next_due"],
                              "failure_count": state["failure_count"], "last_error": state["last_error"],
                              "last_checked": state["last_checked"]}
        return result

    def sync_source_minimums(self, minimums: dict[str, int]):
        self._expire_black_week()
        now = time.time()
        settings = self.get_settings()
        touched = set()
        with self.conn:
            for state in self.conn.execute("SELECT * FROM watch_sources"):
                source = state["source_id"]
                if source not in minimums or state["min_interval_seconds"] == minimums[source]:
                    continue
                watch = self._watch(self.conn.execute("SELECT * FROM watches WHERE id=?", (state["watch_id"],)).fetchone())
                minimum = max(0, minimums[source])
                if state["last_checked"] is None:
                    due = state["next_due"]
                else:
                    candidate = state["last_checked"] + interval(watch, settings, source, minimum, now) * min(
                        2 ** state["failure_count"], 16)
                    due = max(state["next_due"], candidate) if state["failure_count"] else candidate
                self.conn.execute("UPDATE watch_sources SET min_interval_seconds=?,next_due=? WHERE watch_id=? AND source_id=?",
                                  (minimum, due, state["watch_id"], source))
                touched.add(state["watch_id"])
            for watch_id in touched:
                self.conn.execute("UPDATE watches SET next_due=(SELECT MIN(next_due) FROM watch_sources WHERE watch_id=?) WHERE id=?",
                                  (watch_id, watch_id))

    def due_watches(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = [w for w in self.list_watches() if not w["paused"] and w["next_due"] <= now]
        rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        return sorted(rows, key=lambda w: (rank[w["priority"]], w["next_due"], w["id"]))

    def get_settings(self) -> dict:
        r = self.conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        return {"paused": bool(r["paused"]), "black_friday_until": r["black_friday_until"],
                "notification_enabled": bool(r["notification_enabled"])}

    def _reschedule_sources(self, settings: dict, now: float):
        for raw in self.conn.execute("SELECT * FROM watches"):
            watch = self._watch(raw)
            for source in self.source_schedules(watch["id"]).values():
                if source["last_checked"] is None:
                    continue
                delay = interval(watch, settings, source["source_id"], source["min_interval_seconds"], now)
                candidate = source["last_checked"] + delay * min(2 ** source["failure_count"], 16)
                due = max(source["next_due"], candidate) if source["failure_count"] else candidate
                self.conn.execute("UPDATE watch_sources SET next_due=? WHERE watch_id=? AND source_id=?",
                                  (due, watch["id"], source["source_id"]))
            self.conn.execute("UPDATE watches SET next_due=(SELECT MIN(next_due) FROM watch_sources WHERE watch_id=?) WHERE id=?",
                              (watch["id"], watch["id"]))

    def _expire_black_week(self):
        now = time.time()
        settings = self.get_settings()
        if settings["black_friday_until"] and settings["black_friday_until"] <= now:
            with self.conn:
                self.conn.execute("UPDATE settings SET black_friday_until=0 WHERE id=1")
                settings["black_friday_until"] = 0
                self._reschedule_sources(settings, now)

    def update_settings(self, data: dict) -> dict:
        allowed = {"paused", "black_friday_until", "notification_enabled"}
        if set(data) - allowed:
            raise ValueError("unknown settings key")
        if "black_friday_until" in data:
            value = data["black_friday_until"]
            if not isinstance(value, (int, float)) or (value != 0 and not time.time() < value <= time.time() + 7 * 86400):
                raise ValueError("black_friday_until must be within seven days")
        for k in ("paused", "notification_enabled"):
            if k in data and type(data[k]) is not bool:
                raise ValueError(f"{k} must be bool")
        if data:
            keys = list(data)
            with self.conn:
                self.conn.execute("UPDATE settings SET " + ",".join(k + "=?" for k in keys) + " WHERE id=1",
                                  [data[k] for k in keys])
                if "black_friday_until" in data:
                    self._reschedule_sources(self.get_settings(), time.time())
        return self.get_settings()

    def list_offers(self, watch_id: int | None = None) -> list[dict]:
        sql = "SELECT o.*,w.data AS watch_data,w.next_due,w.last_checked FROM offers o JOIN watches w ON w.id=o.watch_id"
        args = ()
        if watch_id is not None:
            sql += " WHERE o.watch_id=?"
            args = (watch_id,)
        sql += " ORDER BY o.observed_at DESC,o.id DESC"
        now = time.time()
        result = []
        schedules = {}
        for r in self.conn.execute(sql, args):
            data = json.loads(r["data"])
            watch = json.loads(r["watch_data"])
            if r["watch_id"] not in schedules:
                schedules[r["watch_id"]] = self.source_schedules(r["watch_id"])
            source = schedules[r["watch_id"]].get(data["source"])
            stale = (source is None or (source["last_checked"] is not None and r["seen_at"] < source["last_checked"])
                     or (source is not None and now >= source["next_due"]) or data.get("quality") == "feed_stale"
                     or (data.get("provenance") == "feed" and now - r["observed_at"] > FEED_FRESH_SECONDS)
                     or now - r["observed_at"] > 2 * interval(
                         watch, self.get_settings(), data["source"],
                         source["min_interval_seconds"] if source else 0, now))
            result.append({"id": r["id"], "watch_id": r["watch_id"], **data,
                           "stale": stale, "evaluation": r["evaluation"],
                           "comparison_cents": r["comparison_cents"], "savings_cents": r["savings_cents"]})
        return result

    def comparisons(self) -> list[dict]:
        groups: dict[tuple, list[dict]] = {}
        for offer in self.list_offers():
            if (offer["stale"] or offer["availability"] != "in_stock"
                    or offer["total_cents"] is None or offer.get("offer_type", "retail") != "retail"):
                continue
            identity = comparison_identity(offer)
            if identity is not None:
                groups.setdefault(identity, []).append(offer)
        result = []
        for identity, offers in groups.items():
            if len({offer["merchant"].casefold() for offer in offers}) < 2:
                continue
            ranked = sorted(offers, key=lambda offer: (offer["total_cents"], offer["id"]))
            result.append({"identity_basis": identity[0], "identity_value": identity[1],
                           "offer_ids": [offer["id"] for offer in ranked],
                           "lowest_total_cents": ranked[0]["total_cents"]})
        return sorted(result, key=lambda group: (group["lowest_total_cents"], group["identity_value"]))

    def history(self, offer_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT checked_at,price_cents,shipping_cents,total_cents,availability,provenance FROM observations WHERE offer_id=? ORDER BY checked_at,id", (offer_id,))]

    def list_events(self) -> list[dict]:
        return [{"id": r["id"], "watch_id": r["watch_id"], **json.loads(r["data"]),
                 "notification_status": r["notification_status"] or "disabled",
                 "notification_attempts": r["notification_attempts"] or 0,
                 "notification_error": r["notification_error"]}
                for r in self.conn.execute("""SELECT e.*,b.status AS notification_status,
                    b.attempts AS notification_attempts,b.last_error AS notification_error
                    FROM events e LEFT JOIN outbox b ON b.event_id=e.id ORDER BY e.id DESC LIMIT 200""")]

    def list_checks(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM checks ORDER BY id DESC LIMIT 200")]

    def record_source_status(self, source: str, error: str | None, checked_at: float):
        with self.conn:
            self.conn.execute("""INSERT INTO source_status(source_id,last_checked,last_success,last_error)
                VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                last_checked=excluded.last_checked,
                last_success=CASE WHEN excluded.last_error IS NULL THEN excluded.last_checked ELSE source_status.last_success END,
                last_error=excluded.last_error""",
                (source, checked_at, checked_at if error is None else None, error))

    def source_statuses(self) -> dict[str, dict]:
        return {r["source_id"]: {"last_checked": r["last_checked"],
                                  "last_success": r["last_success"], "last_error": r["last_error"]}
                for r in self.conn.execute("SELECT * FROM source_status")}

    def prior(self, watch_id: int, key: str) -> tuple[list[dict], int | None, bool]:
        row = self.conn.execute("SELECT id FROM offers WHERE watch_id=? AND offer_key=?", (watch_id, key)).fetchone()
        if not row:
            return [], None, False
        history = self.history(row["id"])
        alert = self.conn.execute("SELECT total_cents FROM events WHERE watch_id=? AND offer_key=? ORDER BY id DESC LIMIT 1",
                                  (watch_id, key)).fetchone()
        return history, alert[0] if alert else None, bool(alert)

    def record_check(self, watch_id: int, observations: list[tuple[Offer, str, int | None, int | None, str | None]],
                     status: str, error: str | None, checked_at: float,
                     source_outcomes: dict[str, tuple[str | None, int]] | None = None):
        """Persist observations and event/outbox decisions as one unit."""
        row = self.get_watch(watch_id)
        if row is None:
            return
        if source_outcomes is None:
            source_outcomes = {source: (error if status == "error" else None, 0) for source in row["source_ids"]}
        with self.conn:
            for item, evaluation, comparison, savings, reason in observations:
                data = item.model_dump()
                existing = self.conn.execute("SELECT id FROM offers WHERE watch_id=? AND offer_key=?",
                                             (watch_id, item.key)).fetchone()
                if existing:
                    oid = existing[0]
                    self.conn.execute("UPDATE offers SET data=?,evaluation=?,comparison_cents=?,savings_cents=?,observed_at=? WHERE id=? AND observed_at<?",
                                      (json.dumps(data), evaluation, comparison, savings, item.checked_at, oid, item.checked_at))
                    self.conn.execute("UPDATE offers SET seen_at=? WHERE id=?", (checked_at, oid))
                else:
                    oid = self.conn.execute("INSERT INTO offers(watch_id,offer_key,data,evaluation,comparison_cents,savings_cents,observed_at,seen_at) VALUES(?,?,?,?,?,?,?,?)",
                                            (watch_id, item.key, json.dumps(data), evaluation, comparison, savings, item.checked_at, checked_at)).lastrowid
                self.conn.execute("INSERT OR IGNORE INTO observations(offer_id,checked_at,price_cents,shipping_cents,total_cents,availability,provenance) VALUES(?,?,?,?,?,?,?)",
                                  (oid, item.checked_at, item.price_cents, item.shipping_cents, item.total_cents,
                                   item.availability, item.provenance))
                if reason and item.total_cents is not None:
                    event = {"product": item.name, "name": item.name, "merchant": item.merchant,
                             "total_cents": item.total_cents, "comparison_cents": comparison,
                             "savings_cents": savings, "url": item.url, "checked_at": item.checked_at,
                             "availability": item.availability, "reason": reason,
                             "source": item.source, "provenance": item.provenance, "quality": item.quality}
                    cur = self.conn.execute("INSERT OR IGNORE INTO events(watch_id,offer_key,total_cents,data,created_at,last_alerted_total) VALUES(?,?,?,?,?,?)",
                                            (watch_id, item.key, item.total_cents, json.dumps(event), checked_at, item.total_cents))
                    if cur.rowcount and self.get_settings()["notification_enabled"]:
                        self.conn.execute("INSERT INTO outbox(event_id,next_due) VALUES(?,?)", (cur.lastrowid, checked_at))
            self.conn.execute("INSERT INTO checks(watch_id,checked_at,status,offers,error) VALUES(?,?,?,?,?)",
                              (watch_id, checked_at, status, len(observations), error))
            for source, (source_error, minimum) in source_outcomes.items():
                previous = self.conn.execute("SELECT failure_count FROM watch_sources WHERE watch_id=? AND source_id=?",
                                             (watch_id, source)).fetchone()
                if previous is None:
                    continue
                failure = previous[0] + 1 if source_error else 0
                delay = interval(row, self.get_settings(), source, minimum, checked_at) * (min(2 ** failure, 16) if failure else 1)
                self.conn.execute("""UPDATE watch_sources SET next_due=?,failure_count=?,last_error=?,last_checked=?,min_interval_seconds=?
                    WHERE watch_id=? AND source_id=?""",
                    (checked_at + delay, failure, source_error, checked_at, minimum, watch_id, source))
                self.conn.execute("""INSERT INTO source_status(source_id,last_checked,last_success,last_error)
                    VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                    last_checked=excluded.last_checked,
                    last_success=CASE WHEN excluded.last_error IS NULL THEN excluded.last_checked ELSE source_status.last_success END,
                    last_error=excluded.last_error""",
                    (source, checked_at, checked_at if source_error is None else None, source_error))
            current = self.source_schedules(watch_id)
            errors = [f"{source}: {state['last_error']}" for source, state in current.items() if state["last_error"]]
            self.conn.execute("UPDATE watches SET next_due=?,failure_count=?,last_error=?,last_checked=? WHERE id=?",
                              (min(s["next_due"] for s in current.values()),
                               max(s["failure_count"] for s in current.values()), error or "; ".join(errors) or None,
                               checked_at, watch_id))

    def due_outbox(self, now: float | None = None, limit: int = 20) -> list[dict]:
        now = time.time() if now is None else now
        return [{"id": r["id"], **json.loads(r["data"])} for r in self.conn.execute(
            "SELECT b.id,e.data FROM outbox b JOIN events e ON e.id=b.event_id WHERE b.status='pending' AND b.next_due<=? ORDER BY b.id LIMIT ?", (now, limit))]

    def outbox_result(self, id: int, error: str | None):
        with self.conn:
            if error is None:
                self.conn.execute("UPDATE outbox SET status='sent',last_error=NULL WHERE id=?", (id,))
            else:
                row = self.conn.execute("SELECT attempts FROM outbox WHERE id=?", (id,)).fetchone()
                if row is not None:
                    delay = min(60 * 2 ** row["attempts"], 3600)
                    self.conn.execute("UPDATE outbox SET attempts=attempts+1,next_due=?,last_error=? WHERE id=?",
                                      (time.time() + delay, error[:300], id))

    def close(self):
        self.conn.close()
