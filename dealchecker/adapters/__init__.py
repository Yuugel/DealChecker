"""Source registry; only explicitly configured authorized feeds are added."""

from __future__ import annotations

from .apple import AppleAdapter
from .feed import FeedAdapter


def build_registry(client, feeds: list[dict] | None = None) -> dict:
    registry = {"apple": AppleAdapter(client)}
    for config in feeds or []:
        adapter = FeedAdapter(client, config)
        if adapter.id in registry:
            raise ValueError("Doppelte Quellen-ID")
        registry[adapter.id] = adapter
    return registry


def source_catalog(registry: dict) -> list[dict]:
    return [
        {"id": adapter.id, "name": adapter.name, "description": adapter.description,
         "min_interval_seconds": adapter.min_interval_seconds, "configured": True}
        for adapter in registry.values()
    ]
