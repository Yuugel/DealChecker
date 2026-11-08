"""Modula24's documented public German product feed for furniture parts."""

from __future__ import annotations

import re
from time import monotonic, time
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .base import safe_https_url
from .feed import FeedAdapter


_FEED_URL = "https://modula24.com/feeds/products.de.json"
_HOSTS = {"modula24.com"}
_VARIANT_ID = re.compile(r"[1-9][0-9]{0,19}\Z")
_PRICE = re.compile(r"([0-9]{1,10})\.([0-9]{2}) EUR\Z")
_PRODUCT_PATH = re.compile(r"/products/[a-z0-9-]+\Z")
_MAX_ROWS = 5000
_MAX_BODY = 4 * 1024 * 1024


def _cents(value: object, *, allow_zero: bool = False) -> int:
    if not isinstance(value, str) or (match := _PRICE.fullmatch(value)) is None:
        raise ValueError("Modula24-Preis ist ungültig oder nicht in EUR")
    cents = int(match.group(1)) * 100 + int(match.group(2))
    if not (cents >= 0 if allow_zero else cents > 0) or cents > 1_000_000_000_000:
        raise ValueError("Modula24-Preis ist ungültig")
    return cents


def _product_url(value: object, variant_id: str) -> str:
    url = safe_https_url(value, hosts=_HOSTS)
    parsed = urlsplit(url)
    if not _PRODUCT_PATH.fullmatch(parsed.path):
        raise ValueError("Modula24-Link ist keine Produktseite")
    if parsed.query:
        query = parse_qs(parsed.query, keep_blank_values=True)
        if query != {"variant": [variant_id]}:
            raise ValueError("Modula24-Link und Variante widersprechen sich")
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, f"variant={variant_id}", ""))


def _shipping_cents(value: object) -> int | None:
    if not isinstance(value, list):
        raise ValueError("Modula24-Versanddaten sind ungültig")
    result = None
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("countries"), list):
            raise ValueError("Modula24-Versanddaten sind ungültig")
        countries = entry["countries"]
        if any(not isinstance(country, str) or len(country) != 2 for country in countries):
            raise ValueError("Modula24-Versandländer sind ungültig")
        if "DE" in countries:
            if result is not None:
                raise ValueError("Modula24 nennt mehrere DE-Versandpreise")
            result = _cents(entry.get("price"), allow_zero=True)
    return result


class Modula24Adapter(FeedAdapter):
    """One merchant, one finite feed; a catalog refresh verifies exact variants."""

    def __init__(self, client):
        super().__init__(client, {"id": "modula24", "name": "Modula24", "url": _FEED_URL})
        self.description = "Möbelteile und Werkzeuge von Modula24 DE; kein allgemeiner Preisvergleich"

    def _feed_offer(self, item: object, fetched_at: float):
        if not isinstance(item, dict):
            raise ValueError("Modula24-Feed enthält einen ungültigen Artikel")
        variant_id = item.get("variant_id")
        if (not isinstance(variant_id, str) or not _VARIANT_ID.fullmatch(variant_id)
                or item.get("id") != f"M24-{variant_id}"):
            raise ValueError("Modula24-Artikel hat eine widersprüchliche Varianten-ID")
        options = item.get("options", {})
        if not isinstance(options, dict) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
            for key, value in options.items()
        ):
            raise ValueError("Modula24-Variantenangaben sind ungültig")
        variant = "; ".join(f"{key.strip()}: {value.strip()}" for key, value in sorted(options.items()))
        availability = item.get("availability")
        if availability not in {"in_stock", "out_of_stock"}:
            raise ValueError("Modula24-Verfügbarkeit ist ungültig")
        return self._offer({
            "product_id": variant_id,
            "name": item.get("title"),
            "url": _product_url(item.get("link"), variant_id),
            "price_cents": _cents(item.get("price")),
            "shipping_cents": _shipping_cents(item.get("shipping")),
            "currency": "EUR",
            "availability": availability,
            "merchant": "Modula24",
            "variant": variant,
            "manufacturer": item.get("brand", ""),
            "category": item.get("product_type", ""),
        }, fetched_at)

    async def _catalog(self):
        async with self._lock:
            if self._cached is not None and monotonic() < self._expires:
                return self._cached
            data = await self.client.get_json(self.url, allowed_hosts=_HOSTS, max_body=_MAX_BODY)
            if (not isinstance(data, dict) or data.get("currency") != "EUR"
                    or data.get("language") != "de" or not isinstance(data.get("generated"), str)
                    or not isinstance(data.get("items"), list)):
                raise ValueError("Modula24-Feed hat ein ungültiges Format")
            rows = data["items"]
            if len(rows) > _MAX_ROWS:
                raise ValueError("Modula24-Feed enthält zu viele Artikel")
            fetched_at = time()  # generated is a change timestamp, not this retrieval's observation.
            offers = [self._feed_offer(row, fetched_at) for row in rows]
            if len({offer.product_id for offer in offers}) != len(offers):
                raise ValueError("Modula24-Feed enthält doppelte Varianten-IDs")
            self._cached = offers
            self._expires = monotonic() + self.min_interval_seconds
            return offers
