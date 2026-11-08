"""Source registry; only explicitly configured authorized feeds are added."""

from __future__ import annotations

from .apple import AppleAdapter
from .feed import FeedAdapter
from .modula24 import Modula24Adapter
from .bestenvergleich import BestenvergleichAdapter
from .alternate import AlternateAdapter
from .berrybase import BerryBaseAdapter
from .lidl import LidlAdapter


def build_registry(client, feeds: list[dict] | None = None) -> dict:
    registry = {"apple": AppleAdapter(client), "modula24": Modula24Adapter(client),
                "bestenvergleich": BestenvergleichAdapter(client), "alternate": AlternateAdapter(client),
                "berrybase": BerryBaseAdapter(client), "lidl": LidlAdapter(client)}
    for config in feeds or []:
        adapter = FeedAdapter(client, config)
        if adapter.id in registry:
            raise ValueError("Doppelte Quellen-ID")
        registry[adapter.id] = adapter
    return registry


def source_catalog(registry: dict) -> list[dict]:
    from dealchecker.links import url_hosts
    return [
        {"id": adapter.id, "name": adapter.name, "description": adapter.description,
         "min_interval_seconds": adapter.min_interval_seconds, "configured": True,
         "supports_search": getattr(adapter, 'supports_search', True), "url_hosts": list(url_hosts(adapter))}
        for adapter in registry.values()
    ]
