"""Adaptive watch cadence with source limits and explicit overrides."""
import time


OLD_DEFAULTS = {"critical": 300, "high": 900, "normal": 1800, "low": 3600}
EXACT_DEFAULTS = {
    "normal": (25200, 2700),
    "high": (2700, 600),
    "critical": (1800, 300),
    "low": (28800, 3600),
}
DISCOVERY_DEFAULT = (28800, 10800)


def interval_pair(watch: dict, source_id: str | None = None, minimum: int = 0) -> tuple[int, int]:
    normal, week = DISCOVERY_DEFAULT if watch["kind"] == "discovery" else EXACT_DEFAULTS[watch["priority"]]
    if watch.get("interval_seconds") is not None:
        normal = watch["interval_seconds"]
    if watch.get("black_friday_interval_seconds") is not None:
        week = watch["black_friday_interval_seconds"]
    overrides = watch.get("source_intervals") or {}
    source = overrides.get(source_id) if source_id else None
    if source:
        normal = source["normal_seconds"]
        week = source["black_friday_seconds"]
    return max(300, normal, minimum), max(300, week, minimum)


def interval(watch: dict, settings: dict | None = None, source_id: str | None = None,
             minimum: int = 0, now: float | None = None) -> int:
    now = time.time() if now is None else now
    normal, week = interval_pair(watch, source_id, minimum)
    return week if settings and settings["black_friday_until"] > now else normal
