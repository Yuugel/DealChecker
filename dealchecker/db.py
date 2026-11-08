"""SQLite state. Every completed check is committed in one transaction."""
import json
import sqlite3
import time
from pathlib import Path

from .models import Offer, WatchInput

SCHEMA_VERSION = 2
INTERVALS = {"critical": 300, "high": 900, "normal": 1800, "low": 3600}


def interval(watch: dict, settings: dict | None = None) -> int:
    base = watch.get("interval_seconds") or INTERVALS[watch["priority"]]
    if settings and settings["black_friday_until"] > time.time():
        return max(300, base // 2)
    return base


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

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
        return self.get_watch(cur.lastrowid)

    def update_watch(self, id: int, data: WatchInput) -> dict | None:
        with self.conn:
            cur = self.conn.execute("UPDATE watches SET data=?,next_due=? WHERE id=?",
                                    (data.model_dump_json(), time.time(), id))
        return self.get_watch(id) if cur.rowcount else None

    def delete_watch(self, id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM watches WHERE id=?", (id,))
        return bool(cur.rowcount)

    def get_watch(self, id: int) -> dict | None:
        return self._watch(self.conn.execute("SELECT * FROM watches WHERE id=?", (id,)).fetchone())

    def list_watches(self) -> list[dict]:
        return [self._watch(r) for r in self.conn.execute("SELECT * FROM watches ORDER BY id DESC")]

    def force_due(self, id: int, when: float):
        with self.conn:
            self.conn.execute("UPDATE watches SET next_due=? WHERE id=?", (when, id))

    def due_watches(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        rows = [w for w in self.list_watches() if not w["paused"] and w["next_due"] <= now]
        rank = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        return sorted(rows, key=lambda w: (rank[w["priority"]], w["next_due"], w["id"]))

    def get_settings(self) -> dict:
        r = self.conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        return {"paused": bool(r["paused"]), "black_friday_until": r["black_friday_until"],
                "notification_enabled": bool(r["notification_enabled"])}

    def update_settings(self, data: dict) -> dict:
        allowed = {"paused", "black_friday_until", "notification_enabled"}
        if set(data) - allowed:
            raise ValueError("unknown settings key")
        if "black_friday_until" in data:
            value = data["black_friday_until"]
            if not isinstance(value, (int, float)) or (value != 0 and not time.time() < value <= time.time() + 86400):
                raise ValueError("black_friday_until must be within 24 hours")
        for k in ("paused", "notification_enabled"):
            if k in data and type(data[k]) is not bool:
                raise ValueError(f"{k} must be bool")
        if data:
            keys = list(data)
            with self.conn:
                self.conn.execute("UPDATE settings SET " + ",".join(k + "=?" for k in keys) + " WHERE id=1",
                                  [data[k] for k in keys])
                if "black_friday_until" in data and data["black_friday_until"] > time.time():
                    settings = self.get_settings()
                    for row in self.list_watches():
                        if row["last_checked"] is None:
                            continue
                        delay = interval(row, settings) * min(2 ** row["failure_count"], 16)
                        candidate = row["last_checked"] + delay
                        self.conn.execute("UPDATE watches SET next_due=MIN(next_due,?) WHERE id=?", (candidate, row["id"]))
        return self.get_settings()

    def list_offers(self, watch_id: int | None = None) -> list[dict]:
        sql = "SELECT o.*,w.data AS watch_data,w.next_due,w.last_error FROM offers o JOIN watches w ON w.id=o.watch_id"
        args = ()
        if watch_id is not None:
            sql += " WHERE o.watch_id=?"
            args = (watch_id,)
        sql += " ORDER BY o.observed_at DESC,o.id DESC"
        now = time.time()
        result = []
        for r in self.conn.execute(sql, args):
            data = json.loads(r["data"])
            watch = json.loads(r["watch_data"])
            stale = (r["last_error"] is not None or now >= r["next_due"] or data.get("quality") == "feed_stale"
                     or now - r["observed_at"] > 2 * interval(watch, self.get_settings()))
            result.append({"id": r["id"], "watch_id": r["watch_id"], **data,
                           "stale": stale, "evaluation": r["evaluation"],
                           "comparison_cents": r["comparison_cents"], "savings_cents": r["savings_cents"]})
        return result

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

    def prior(self, watch_id: int, key: str) -> tuple[list[dict], int | None, bool]:
        row = self.conn.execute("SELECT id FROM offers WHERE watch_id=? AND offer_key=?", (watch_id, key)).fetchone()
        if not row:
            return [], None, False
        history = self.history(row["id"])
        alert = self.conn.execute("SELECT total_cents FROM events WHERE watch_id=? AND offer_key=? ORDER BY id DESC LIMIT 1",
                                  (watch_id, key)).fetchone()
        return history, alert[0] if alert else None, bool(alert)

    def record_check(self, watch_id: int, observations: list[tuple[Offer, str, int | None, int | None, str | None]],
                     status: str, error: str | None, checked_at: float):
        """Persist observations and event/outbox decisions as one unit."""
        row = self.get_watch(watch_id)
        if row is None:
            return
        failure = row["failure_count"] + 1 if status == "error" else 0
        delay = interval(row, self.get_settings()) * (min(2 ** failure, 16) if failure else 1)
        with self.conn:
            for item, evaluation, comparison, savings, reason in observations:
                data = item.model_dump()
                existing = self.conn.execute("SELECT id FROM offers WHERE watch_id=? AND offer_key=?",
                                             (watch_id, item.key)).fetchone()
                if existing:
                    oid = existing[0]
                    self.conn.execute("UPDATE offers SET data=?,evaluation=?,comparison_cents=?,savings_cents=?,observed_at=? WHERE id=? AND observed_at<?",
                                      (json.dumps(data), evaluation, comparison, savings, item.checked_at, oid, item.checked_at))
                else:
                    oid = self.conn.execute("INSERT INTO offers(watch_id,offer_key,data,evaluation,comparison_cents,savings_cents,observed_at) VALUES(?,?,?,?,?,?,?)",
                                            (watch_id, item.key, json.dumps(data), evaluation, comparison, savings, item.checked_at)).lastrowid
                self.conn.execute("INSERT OR IGNORE INTO observations(offer_id,checked_at,price_cents,shipping_cents,total_cents,availability,provenance) VALUES(?,?,?,?,?,?,?)",
                                  (oid, item.checked_at, item.price_cents, item.shipping_cents, item.total_cents,
                                   item.availability, item.provenance))
                if reason and item.total_cents is not None:
                    event = {"product": item.name, "name": item.name, "merchant": item.merchant,
                             "total_cents": item.total_cents, "comparison_cents": comparison,
                             "savings_cents": savings, "url": item.url, "checked_at": item.checked_at,
                             "availability": item.availability, "reason": reason}
                    cur = self.conn.execute("INSERT OR IGNORE INTO events(watch_id,offer_key,total_cents,data,created_at,last_alerted_total) VALUES(?,?,?,?,?,?)",
                                            (watch_id, item.key, item.total_cents, json.dumps(event), checked_at, item.total_cents))
                    if cur.rowcount and self.get_settings()["notification_enabled"]:
                        self.conn.execute("INSERT INTO outbox(event_id,next_due) VALUES(?,?)", (cur.lastrowid, checked_at))
            self.conn.execute("INSERT INTO checks(watch_id,checked_at,status,offers,error) VALUES(?,?,?,?,?)",
                              (watch_id, checked_at, status, len(observations), error))
            self.conn.execute("UPDATE watches SET next_due=?,failure_count=?,last_error=?,last_checked=? WHERE id=?",
                              (checked_at + delay, failure, error, checked_at, watch_id))

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
