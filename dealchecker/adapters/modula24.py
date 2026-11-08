"""Modula24's documented public German product feed for furniture parts."""

from __future__ import annotations

import re
import asyncio
from collections import OrderedDict
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
_SHIPPING_URL = "https://modula24.com/api/checkout/shipping"
_MAX_KNOWN = 1000
_MAX_TARGETS = 128


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


def _plain_cents(value: object, *, allow_zero: bool = False) -> int:
    return _cents(f"{value} EUR", allow_zero=allow_zero)


def _target_location(value: str) -> tuple[str, str]:
    url = safe_https_url(value, hosts=_HOSTS)
    parsed = urlsplit(url)
    if not _PRODUCT_PATH.fullmatch(parsed.path):
        raise ValueError("Modula24-Link ist keine Produktseite")
    variant_id = ""
    if parsed.query:
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"variant"} or len(query["variant"]) != 1 or not _VARIANT_ID.fullmatch(query["variant"][0]):
            raise ValueError("Modula24-Link enthält keine eindeutige Variante")
        variant_id = query["variant"][0]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), variant_id


def _quote_shipping(data: object) -> int | None:
    if (not isinstance(data, dict) or data.get("country") != "DE" or data.get("currency") != "EUR"
            or not isinstance(data.get("shipping_options"), list)):
        raise ValueError("Modula24-Versandangebot hat ein ungültiges Format oder eine falsche Währung")
    if any(not isinstance(option, dict) or not isinstance(option.get("id"), str)
           for option in data["shipping_options"]):
        raise ValueError("Modula24-Versandoptionen sind ungültig")
    matches = [option for option in data["shipping_options"]
               if isinstance(option, dict) and option.get("id") == "dhl"]
    if len(matches) > 1:
        raise ValueError("Modula24 nennt mehrere DHL-Versandpreise")
    return _plain_cents(matches[0].get("cost"), allow_zero=True) if matches else None


class Modula24Adapter(FeedAdapter):
    """Authorized catalog discovery and targeted variant/DE shipping refresh."""

    def __init__(self, client):
        super().__init__(client, {"id": "modula24", "name": "Modula24", "url": _FEED_URL})
        self.description = "Möbelteile und Werkzeuge von Modula24 DE; kein allgemeiner Preisvergleich"
        self._target_lock = asyncio.Lock()
        self._target_cache = OrderedDict()
        self._target_expires: dict[tuple[str, str], float] = {}
        self._known = OrderedDict()

    def _remember(self, variant_id: str, url: str) -> None:
        self._known[variant_id] = url
        self._known.move_to_end(variant_id)
        while len(self._known) > _MAX_KNOWN:
            self._known.popitem(last=False)

    @staticmethod
    def _variant(product: dict, item: dict) -> str:
        options = product.get("options")
        if not isinstance(options, list) or len(options) > 3:
            raise ValueError("Modula24-Produktoptionen sind ungültig")
        parts = []
        for option in options:
            if not isinstance(option, dict) or not isinstance(option.get("name"), str):
                raise ValueError("Modula24-Produktoptionen sind ungültig")
            position = option.get("position")
            if isinstance(position, bool) or position not in (1, 2, 3):
                raise ValueError("Modula24-Produktoptionen sind ungültig")
            name = option["name"].strip()
            value = item.get(f"option{position}")
            if name == "Title" and value == "Default Title":
                continue
            if not name or not isinstance(value, str) or not value.strip():
                raise ValueError("Modula24-Variantenangaben sind ungültig")
            parts.append((name, value.strip()))
        if len({name for name, _ in parts}) != len(parts):
            raise ValueError("Modula24-Produktoptionen sind doppelt")
        return "; ".join(f"{name}: {value}" for name, value in sorted(parts))

    async def _target(self, base_url: str, requested_id: str, expected_variant: str):
        key = (base_url, requested_id)
        async with self._target_lock:
            if key in self._target_cache and monotonic() < self._target_expires.get(key, 0):
                offer = self._target_cache[key]
                if expected_variant and offer.variant.casefold() != expected_variant.casefold():
                    raise ValueError("Modula24-Variante widerspricht der Überwachung")
                self._target_cache.move_to_end(key)
                return offer
            data = await self.client.get_json(base_url + ".json", allowed_hosts=_HOSTS)
            product = data.get("product") if isinstance(data, dict) else None
            if not isinstance(product, dict) or product.get("handle") != base_url.rsplit("/", 1)[-1]:
                raise ValueError("Modula24-Produktidentität widerspricht dem Link")
            if product.get("currency", "EUR") != "EUR":
                raise ValueError("Modula24-Produkt hat eine falsche Währung")
            variants = product.get("variants")
            if not isinstance(variants, list) or not 1 <= len(variants) <= 1000:
                raise ValueError("Modula24-Produkt hat ungültige Varianten")
            ids = [str(item.get("id")) if isinstance(item, dict) else "" for item in variants]
            if any(not _VARIANT_ID.fullmatch(item_id) for item_id in ids) or len(set(ids)) != len(ids):
                raise ValueError("Modula24-Produkt hat widersprüchliche Varianten-IDs")
            if not requested_id:
                if len(variants) != 1:
                    raise ValueError("Modula24-Produkt hat mehrere Varianten; bitte Varianten-ID auswählen")
                requested_id = ids[0]
            if requested_id not in ids:
                raise ValueError("Modula24-Variante wurde für dieses Produkt nicht gefunden")
            item = variants[ids.index(requested_id)]
            variant = self._variant(product, item)
            if expected_variant and variant.casefold() != expected_variant.casefold():
                raise ValueError("Modula24-Variante widerspricht der Überwachung")
            available = item.get("available")
            if not isinstance(available, bool):
                raise ValueError("Modula24-Verfügbarkeit ist ungültig")
            title = product.get("title")
            variant_title = item.get("title")
            if not isinstance(title, str) or not title.strip() or not isinstance(variant_title, str):
                raise ValueError("Modula24-Produktname ist ungültig")
            name = title.strip() if variant_title in ("", "Default Title") else f"{title.strip()} - {variant_title.strip()}"
            price = _plain_cents(item.get("price"))
            shipping = None
            if available:
                quote = await self.client.post_json_response(
                    _SHIPPING_URL,
                    {"country": "DE", "items": [{"id": requested_id, "qty": 1}], "currency": "EUR"},
                    allowed_hosts=_HOSTS,
                )
                shipping = _quote_shipping(quote)
            fetched_at = time()
            offer = self._offer({
                "product_id": requested_id, "name": name,
                "url": _product_url(base_url, requested_id), "price_cents": price,
                "shipping_cents": shipping, "currency": "EUR",
                "availability": "in_stock" if available else "out_of_stock",
                "merchant": "Modula24", "variant": variant,
                "manufacturer": product.get("vendor", ""), "category": product.get("product_type", ""),
            }, fetched_at).model_copy(update={"provenance": "live", "quality": "source_reported"})
            self._target_cache[key] = offer
            self._target_expires[key] = monotonic() + self.min_interval_seconds
            self._target_cache.move_to_end(key)
            while len(self._target_cache) > _MAX_TARGETS:
                old_key, _ = self._target_cache.popitem(last=False)
                self._target_expires.pop(old_key, None)
            self._remember(requested_id, offer.url)
            return offer

    async def lookup(self, watch):
        if watch.kind != "exact":
            raise ValueError("Modula24-Abfrage erfordert eine exakte Produktüberwachung")
        if watch.gtin or watch.model:
            raise ValueError("Modula24 kann GTIN oder Modell nicht verifizieren")
        if watch.product_id and not _VARIANT_ID.fullmatch(watch.product_id):
            raise ValueError("Modula24-Varianten-ID ist ungültig")
        if watch.url:
            base_url, url_id = _target_location(watch.url)
            if watch.product_id and url_id and watch.product_id != url_id:
                raise ValueError("Modula24-Link und Varianten-ID widersprechen sich")
            variant_id = watch.product_id or url_id
            if variant_id in self._known and _target_location(self._known[variant_id])[0] != base_url:
                raise ValueError("Modula24-Produktidentität und URL widersprechen sich")
        else:
            variant_id = watch.product_id
            if variant_id not in self._known:
                offers = await self._catalog()
                if variant_id:
                    matches = [offer for offer in offers if offer.product_id == variant_id]
                else:
                    title = watch.query or watch.name
                    matches = [offer for offer in offers if offer.name.casefold() == title.casefold()]
                    if len(matches) > 1:
                        raise ValueError("Modula24-Titel ist mehrdeutig; bitte Varianten-ID auswählen")
                if not matches:
                    return []
                variant_id = matches[0].product_id
                self._remember(variant_id, matches[0].url)
            base_url, _ = _target_location(self._known[variant_id])
        return [await self._target(base_url, variant_id, watch.variant)]

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
            for offer in offers:
                self._remember(offer.product_id, offer.url)
            self._expires = monotonic() + self.min_interval_seconds
            return offers
