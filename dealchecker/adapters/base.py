"""Small source contract and URL validation shared by adapters."""

from __future__ import annotations

from urllib.parse import urlsplit

from dealchecker.network import validate_url


def safe_https_url(value: str, *, hosts: set[str] | None = None, no_query: bool = False) -> str:
    validate_url(value, hosts)
    parsed = urlsplit(value)
    if (no_query and parsed.query) or len(value) > 2048:
        raise ValueError("Ungültige URL")
    return value


class Adapter:
    id: str
    name: str
    description: str
    min_interval_seconds: float

    async def search(self, watch):
        raise NotImplementedError

    async def lookup(self, watch):
        raise NotImplementedError
