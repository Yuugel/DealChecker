"""Explicitly authorized finite public JSON product catalogs."""

from __future__ import annotations

import asyncio
import math
import re
from time import monotonic, time

from dealchecker.models import Offer, WatchInput
from dealchecker.network import validate_url

from .base import Adapter, safe_https_url


_ID = re.compile(r"[a-z][a-z0-9_-]{1,39}\Z")
_REQUIRED = {
    "product_id", "name", "url", "price_cents", "shipping_cents", "currency",
    "availability", "merchant",
}
_OPTIONAL = {"variant", "gtin", "model", "manufacturer", "category", "coupon", "unit_price", "observed_at"}


def _text(value: object, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 500 or (required and not value.strip()):
        raise ValueError(f"Feed-Feld {field} ist ungültig")
    return value.strip()


def _cents(value: object, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1_000_000_000_000:
        raise ValueError(f"Feed-Feld {field} muss nichtnegative ganze Cent enthalten")
    return value


class FeedAdapter(Adapter):
    min_interval_seconds = 300

    def __init__(self, client, config: dict):
        if not isinstance(config, dict) or set(config) != {"id", "name", "url"}:
            raise ValueError("Feed-Konfiguration benötigt id, name und url")
        source_id = config["id"]
        if not isinstance(source_id, str) or not _ID.fullmatch(source_id):
            raise ValueError("Ungültige Feed-ID")
        self.id = source_id
        self.name = _text(config["name"], "name", required=True)
        self.url = safe_https_url(config["url"], no_query=True)
        self.host = validate_url(self.url)
        self.description = "Autorisierter öffentlicher JSON-Produktkatalog; Preise laut Feed"
        self.client = client
        self._cached: list[Offer] | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    def _offer(self, row: dict, fetched_at: float) -> Offer:
        if not isinstance(row, dict) or not _REQUIRED <= row.keys() or row.keys() - (_REQUIRED | _OPTIONAL):
            raise ValueError("Feed-Angebot hat fehlende oder unbekannte Felder")
        url = safe_https_url(row["url"])
        product_id = _text(row["product_id"], "product_id", required=True)
        name = _text(row["name"], "name", required=True)
        merchant = _text(row["merchant"], "merchant", required=True)
        if row["currency"] != "EUR":
            raise ValueError("Feed-Angebot muss in EUR ausgezeichnet sein")
        availability = row["availability"]
        if not isinstance(availability, str) or availability not in {"in_stock", "out_of_stock", "unknown"}:
            raise ValueError("Feed-Angebot hat ungültige Verfügbarkeit")
        observed = row.get("observed_at", fetched_at)
        if (isinstance(observed, bool) or not isinstance(observed, (int, float))
                or not math.isfinite(observed) or observed <= 0 or observed > fetched_at + 60):
            raise ValueError("Feed-Angebot hat ungültigen observed_at-Zeitpunkt")
        quality = "feed_stale" if fetched_at - observed > 2 * self.min_interval_seconds else "feed_reported"
        strings = {key: _text(row.get(key, ""), key) for key in ("variant", "gtin", "model", "manufacturer", "category")}
        nullable = {key: None if row.get(key) is None else _text(row[key], key) for key in ("coupon", "unit_price")}
        return Offer(
            source=self.id, merchant=merchant, product_id=product_id, name=name, url=url,
            price_cents=_cents(row["price_cents"], "price_cents"),
            shipping_cents=_cents(row["shipping_cents"], "shipping_cents", nullable=True),
            currency="EUR", availability=availability, checked_at=float(observed),
            quality=quality, provenance="feed", **strings, **nullable,
        )

    async def _catalog(self) -> list[Offer]:
        async with self._lock:
            if self._cached is not None and monotonic() < self._expires:
                return self._cached
            data = await self.client.get_json(self.url, allowed_hosts={self.host})
            if not isinstance(data, dict) or set(data) != {"offers"} or not isinstance(data["offers"], list):
                raise ValueError("Feed muss ein Objekt mit offers-Liste sein")
            fetched_at = time()
            offers = [self._offer(row, fetched_at) for row in data["offers"]]
            keys = [(offer.merchant.casefold(), offer.product_id.casefold(), offer.variant.casefold()) for offer in offers]
            if len(set(keys)) != len(keys):
                raise ValueError("Feed enthält doppelte Produkt-Varianten")
            self._cached = offers
            self._expires = monotonic() + self.min_interval_seconds
            return offers

    async def search(self, watch: WatchInput) -> list[Offer]:
        if watch.kind != "discovery" or not watch.query:
            raise ValueError("Feed-Suche erfordert einen Suchbegriff")
        terms = [term.casefold() for term in [watch.query, *watch.synonyms] if term]
        return [offer for offer in await self._catalog() if any(
            term in " ".join((offer.name, offer.model, offer.manufacturer, offer.category)).casefold()
            for term in terms
        )]

    async def lookup(self, watch: WatchInput) -> list[Offer]:
        if watch.kind != "exact":
            raise ValueError("Feed-Abfrage erfordert eine exakte Produktüberwachung")
        offers = await self._catalog()
        if watch.gtin and offers and all(not offer.gtin for offer in offers):
            raise ValueError("Feed kann GTIN nicht verifizieren")
        if watch.model and offers and all(not offer.model for offer in offers):
            raise ValueError("Feed kann Modell nicht verifizieren")
        if watch.variant and offers and all(not offer.variant for offer in offers):
            raise ValueError("Feed kann Variante nicht verifizieren")
        if watch.product_id:
            matches = [offer for offer in offers if offer.product_id == watch.product_id]
        elif watch.gtin:
            matches = [offer for offer in offers if offer.gtin == watch.gtin]
        elif watch.model:
            matches = [offer for offer in offers if offer.model.casefold() == watch.model.casefold()]
        elif watch.url:
            safe_https_url(watch.url)
            matches = [offer for offer in offers if offer.url == watch.url]
        else:
            title = watch.query or watch.name
            matches = [offer for offer in offers if offer.name.casefold() == title.casefold()]
            if len(matches) > 1:
                raise ValueError("Feed-Titel ist mehrdeutig; bitte Produkt-ID auswählen")
            if not matches:
                raise ValueError("Feed-Titel nicht gefunden; bitte Produkt-ID auswählen")
        if watch.gtin and matches and any(not offer.gtin for offer in matches):
            raise ValueError("Feed kann GTIN für diesen Treffer nicht verifizieren")
        if watch.model and matches and any(not offer.model for offer in matches):
            raise ValueError("Feed kann Modell für diesen Treffer nicht verifizieren")
        if watch.variant and matches and any(not offer.variant for offer in matches):
            raise ValueError("Feed kann Variante für diesen Treffer nicht verifizieren")
        return [offer for offer in matches if
                (not watch.gtin or offer.gtin == watch.gtin) and
                (not watch.model or offer.model.casefold() == watch.model.casefold()) and
                (not watch.variant or offer.variant.casefold() == watch.variant.casefold())]
