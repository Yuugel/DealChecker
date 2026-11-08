"""German App Store discovery via Apple's documented iTunes Search API."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from time import monotonic, time
from urllib.parse import urlencode, urlsplit
import re

from dealchecker.models import Offer, WatchInput

from .base import Adapter, safe_https_url


_APP_HOSTS = {"apps.apple.com"}
_API_HOSTS = {"itunes.apple.com"}
_ID_PATH = re.compile(r"^/de/(?:[^/]+/)*id([1-9][0-9]*)/?$")


def _cents(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("Apple-Preis fehlt oder ist ungültig")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Apple-Preis ist ungültig") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Apple-Preis ist ungültig")
    cents = amount * 100
    if cents > 1_000_000_000_000 or cents != cents.to_integral_value():
        raise ValueError("Apple-Preis ist ungültig")
    return int(cents)


def _apple_id_from_url(url: str) -> str:
    safe_https_url(url, hosts=_APP_HOSTS)
    parsed = urlsplit(url)
    match = _ID_PATH.fullmatch(parsed.path)
    if not match:
        raise ValueError("Apple-URL muss eine deutsche App-ID enthalten")
    return match.group(1)


class AppleAdapter(Adapter):
    id = "apple"
    name = "Apple App Store DE"
    description = "Apps und Spiele im deutschen Apple App Store; keine In-App-Preise"
    min_interval_seconds = 3.2  # Apple's approximate 20 requests/minute.

    def __init__(self, client):
        self.client = client
        self._cache: OrderedDict[str, tuple[float, list[Offer]]] = OrderedDict()
        self._cache_lock = asyncio.Lock()

    @staticmethod
    def _validate_identity(watch: WatchInput) -> None:
        if watch.gtin:
            raise ValueError("Apple unterstützt keine GTIN-Identität")
        if watch.model:
            raise ValueError("Apple unterstützt keine Modell-Identität")
        if watch.variant:
            raise ValueError("Apple unterstützt keine Varianten-Identität")
        if watch.product_id and not re.fullmatch(r"[1-9][0-9]*", watch.product_id):
            raise ValueError("Apple-Produkt-ID muss numerisch sein")
        if watch.url:
            url_id = _apple_id_from_url(watch.url)
            if watch.product_id and watch.product_id != url_id:
                raise ValueError("Apple-Produkt-ID und URL widersprechen sich")

    @staticmethod
    def _offer(item: dict, checked_at: float) -> Offer:
        if not isinstance(item, dict):
            raise ValueError("Apple lieferte einen ungültigen Treffer")
        track_id = item.get("trackId")
        if isinstance(track_id, bool) or not isinstance(track_id, int) or track_id <= 0:
            raise ValueError("Apple-Treffer ohne gültige ID")
        if item.get("currency") != "EUR":
            raise ValueError("Apple-Treffer ohne EUR-Preis")
        if item.get("kind") != "software":
            raise ValueError("Apple-Treffer ist keine App")
        name = item.get("trackName")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Apple-Treffer ohne Namen")
        url = item.get("trackViewUrl")
        safe_https_url(url, hosts=_APP_HOSTS)
        if _apple_id_from_url(url) != str(track_id):
            raise ValueError("Apple-Treffer mit widersprüchlicher URL")
        artist = item.get("artistName", "")
        genre = item.get("primaryGenreName", "")
        if not isinstance(artist, str) or not isinstance(genre, str):
            raise ValueError("Apple-Treffer mit ungültigen Metadaten")
        return Offer(
            source="apple", merchant="Apple App Store", product_id=str(track_id),
            name=name.strip(), url=url, price_cents=_cents(item.get("price")),
            shipping_cents=0, currency="EUR", availability="in_stock",
            checked_at=checked_at, manufacturer=artist, category=genre,
            quality="source_reported", provenance="live",
        )

    async def _request(self, path: str, params: dict) -> list[Offer]:
        url = f"https://itunes.apple.com/{path}?{urlencode(params)}"
        async with self._cache_lock:
            cached = self._cache.get(url)
            if cached is not None and monotonic() < cached[0]:
                self._cache.move_to_end(url)
                return list(cached[1])
            data = await self.client.get_json(url, allowed_hosts=_API_HOSTS)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("Apple-Antwort hat kein gültiges Ergebnisfeld")
            checked_at = time()
            offers = []
            for item in data["results"]:
                # Arcade subscription entries have no individual purchase price.
                if isinstance(item, dict) and item.get("kind") == "software" and "price" not in item:
                    continue
                offers.append(self._offer(item, checked_at))
            self._cache[url] = (monotonic() + 300, offers)
            self._cache.move_to_end(url)
            if len(self._cache) > 128:
                self._cache.popitem(last=False)
            return list(offers)

    async def search(self, watch: WatchInput) -> list[Offer]:
        self._validate_identity(watch)
        if watch.kind != "discovery":
            raise ValueError("Dynamische Apple-Suche erfordert einen Suchauftrag")
        terms = list(dict.fromkeys(t.strip() for t in [watch.query, *watch.synonyms] if t.strip()))
        if not terms:
            raise ValueError("Suchbegriff fehlt")
        if len(terms) > 6:
            raise ValueError("Apple unterstützt höchstens sechs Suchbegriffe einschließlich Synonyme")
        found: dict[str, Offer] = {}
        for term in terms:
            offers = await self._request("search", {
                "term": term, "country": "de", "media": "software", "entity": "software", "limit": 50,
            })
            for offer in offers:
                found.setdefault(offer.product_id, offer)
        return list(found.values())

    async def lookup(self, watch: WatchInput) -> list[Offer]:
        self._validate_identity(watch)
        if watch.kind != "exact":
            raise ValueError("Exakte Apple-Abfrage erfordert eine Produktüberwachung")
        track_id = watch.product_id or (_apple_id_from_url(watch.url) if watch.url else "")
        if track_id:
            offers = await self._request("lookup", {"id": track_id, "country": "de", "entity": "software"})
            matches = [offer for offer in offers if offer.product_id == track_id]
            if not matches:
                raise ValueError("Apple-Produkt-ID ist nicht verfügbar oder hat keinen Einzelkaufpreis")
            return matches
        title = watch.query or watch.name
        offers = await self._request("search", {
            "term": title, "country": "de", "media": "software", "entity": "software", "limit": 200,
        })
        matches = [offer for offer in offers if offer.name.casefold() == title.casefold()]
        if len(matches) > 1:
            raise ValueError("Apple-Titel ist mehrdeutig; bitte Produkt-ID auswählen")
        if not matches:
            raise ValueError("Apple-Titel nicht eindeutig gefunden; bitte Produkt-ID auswählen")
        return matches
