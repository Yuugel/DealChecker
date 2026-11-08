"""Opt-in ReefAPI retail data for German storefronts."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import json
import re
from time import monotonic, time
from urllib.parse import unquote, urlsplit

from dealchecker.identity import canonical_gtin, matches_product_term
from dealchecker.models import Offer

from .base import Adapter, safe_https_url
from .public_pages import _money, _text


_HOST = "api.reefapi.com"
_AMAZON_DETAIL = "/amazon/v1/product/detail"
_AMAZON_SEARCH = "/amazon/v1/search"
_COSTS = {
    _AMAZON_DETAIL: 2,
    "/amazon/v1/product/offers": 2,
    "/ebay/v1/search": 1,
    "/ebay/v1/item_detail": 2,
    "/mediamarkt/v1/search": 2,
    "/mediamarkt/v1/product/detail": 2,
}
_MAX_SEARCH = 5
_MAX_CACHE = 48
_CACHE_SECONDS = 600
_MAX_BODY = 512 * 1024
_ASIN = re.compile(r"[A-Z0-9]{10}")
_AMAZON_PATH = re.compile(r"/(?:(?:[^/]+/)?dp|gp/product)/([A-Z0-9]{10})(?:/ref=[^/]+)?/?", re.I)


class ReefGateway:
    """Reserve provider credits before each network call and share a bounded cache."""

    def __init__(self, client, settings):
        self.client = client
        self.settings = settings
        self._cache = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return self.settings.configured

    async def request(self, endpoint: str, payload: dict) -> tuple[dict | list, float]:
        if endpoint == _AMAZON_SEARCH:
            cost = payload.get("max_results")
            if type(cost) is not int or not 1 <= cost <= _MAX_SEARCH:
                raise ValueError("ReefAPI-Amazon-Suche benötigt ein begrenztes Ergebnislimit")
        elif endpoint in _COSTS:
            cost = _COSTS[endpoint]
        else:
            raise ValueError("ReefAPI-Endpunkt ist für DealChecker nicht freigegeben")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        async with self._lock:
            secret = self.settings.key()
            if not secret:
                raise ValueError("ReefAPI-Schlüssel fehlt")
            generation = self.settings.generation
            key = (generation, endpoint, encoded)
            cached = self._cache.get(key)
            if cached is not None and monotonic() < cached[0]:
                self._cache.move_to_end(key)
                return deepcopy(cached[1]), cached[2]
            self.settings.reserve(cost)
            response = await self.client.post_json_response(
                f"https://{_HOST}{endpoint}", json.loads(encoded), allowed_hosts={_HOST},
                max_body=_MAX_BODY, headers={"x-api-key": secret},
            )
            if generation != self.settings.generation:
                raise ValueError("ReefAPI-Schlüssel wurde während der Abfrage geändert")
            data, observed_at = self._read_response(endpoint, response)
            self._cache[key] = (monotonic() + _CACHE_SECONDS, data, observed_at)
            self._cache.move_to_end(key)
            if len(self._cache) > _MAX_CACHE:
                self._cache.popitem(last=False)
            return deepcopy(data), observed_at

    @staticmethod
    def _read_response(endpoint: str, response: object) -> tuple[dict | list, float]:
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise ValueError("ReefAPI meldet einen fehlgeschlagenen Abruf")
        meta, data = response.get("meta"), response.get("data")
        _, api, _, action = endpoint.split("/", 3)
        if (not isinstance(meta, dict) or not isinstance(data, (dict, list))
                or meta.get("api") != api or meta.get("endpoint") != action):
            raise ValueError("ReefAPI-Antwort passt nicht zum angefragten Endpunkt")
        received = time()
        timestamp = meta.get("fetched_at")
        if timestamp is None and meta.get("mode") == "live" and meta.get("cache_hit") is False:
            # The observed live provider response can omit its own timestamp.
            return data, received
        try:
            observed = datetime.fromisoformat(timestamp)
            if observed.tzinfo is None:
                raise ValueError
            checked_at = observed.timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            raise ValueError("ReefAPI liefert keinen verifizierbaren Beobachtungszeitpunkt") from None
        if not received - _CACHE_SECONDS <= checked_at <= received + 60:
            raise ValueError("ReefAPI-Beobachtungszeitpunkt ist veraltet oder ungültig")
        return data, checked_at


def _asin(value: object) -> str:
    if not isinstance(value, str) or not _ASIN.fullmatch(value.upper()):
        raise ValueError("Ungültige Amazon-ASIN")
    return value.upper()


def _amazon_asin(url: str) -> str:
    safe_https_url(url, hosts=set(AmazonReefAdapter.url_hosts))
    parsed = urlsplit(url)
    path = unquote(parsed.path, errors="strict")
    if (path.count("/") != parsed.path.count("/") or "\\" in path
            or any(part in {".", ".."} for part in path.split("/"))
            or any(ord(char) < 32 for char in path)
            or not (match := _AMAZON_PATH.fullmatch(path))):
        raise ValueError("Amazon-Link ist keine direkte deutsche Produktseite")
    return _asin(match[1])


def _eu_price(row: dict) -> int:
    code, symbol = row.get("currency_code"), row.get("currency")
    if code not in (None, "EUR") or symbol not in (None, "EUR", "€") or not (code or symbol):
        raise ValueError("ReefAPI-Angebot ist nicht eindeutig in EUR ausgezeichnet")
    return _money(row.get("price"))


def _product_gtin(product: dict) -> str:
    valid = {}
    for key in ("gtin", "ean", "upc", "isbn"):
        value = _text(product.get(key))
        if value and (canonical := canonical_gtin(value)):
            valid.setdefault(canonical, value)
    if len(valid) > 1:
        raise ValueError("ReefAPI-Produkt enthält widersprüchliche Barcodes")
    return next(iter(valid.values()), "")


def _verified_identity(watch, offer: Offer) -> bool:
    if watch.gtin and (not offer.gtin or canonical_gtin(watch.gtin) != canonical_gtin(offer.gtin)):
        return False
    return all(not getattr(watch, field) or getattr(watch, field).casefold() == getattr(offer, field).casefold()
               for field in ("model", "variant"))


class AmazonReefAdapter(Adapter):
    id = "amazon"
    name = "Amazon (ReefAPI)"
    description = "Amazon DE über optionalen ReefAPI-Zugang; Suche liefert Preisreferenzen, Direktlinks die Buy Box"
    min_interval_seconds = 1800
    supports_search = True
    url_hosts = ("www.amazon.de", "amazon.de")

    def __init__(self, gateway: ReefGateway):
        self.gateway = gateway

    @property
    def configured(self) -> bool:
        return self.gateway.configured

    async def _details(self, asin: str) -> Offer:
        data, observed = await self.gateway.request(
            _AMAZON_DETAIL, {"asin": asin, "marketplace": "de", "include_reviews": False},
        )
        item = data.get("product") if isinstance(data, dict) else None
        if (not isinstance(item, dict) or item.get("asin") != asin
                or item.get("asin_mismatch") is True or item.get("served_asin") not in (None, "", asin)
                or item.get("parent_asin") == asin):
            raise ValueError("ReefAPI-Produkt widerspricht der angefragten Amazon-ASIN")
        attributes = item.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        color, size = _text(item.get("color")), _text(item.get("size"))
        category = item.get("categories")
        stock = item.get("in_stock")
        seller = _text(item.get("buybox_seller"))
        if seller.casefold() in {"sold by", "verkauf durch", "rücknahmerichtlinien"}:
            seller = ""
        return Offer(
            source=self.id, merchant="Amazon.de", product_id=asin,
            name=_text(item.get("title"))[:300], url=f"https://www.amazon.de/dp/{asin}",
            price_cents=_eu_price(item), shipping_cents=None, checked_at=observed,
            availability="in_stock" if stock is True else "out_of_stock" if stock is False else "unknown",
            variant="; ".join(part for part in (color, size) if part),
            manufacturer=_text(attributes.get("manufacturer")) or _text(item.get("brand")),
            model=_text(attributes.get("part_number")) or _text(attributes.get("model_number")),
            gtin=_product_gtin(item), seller=seller,
            category=_text(category[-1]) if isinstance(category, list) and category else "",
        )

    async def _rows(self, term: str, limit: int) -> tuple[list, float]:
        data, observed = await self.gateway.request(
            _AMAZON_SEARCH, {"query": term, "marketplace": "de", "max_results": limit},
        )
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("ReefAPI-Amazon-Suche liefert ein ungültiges Ergebnis")
        return rows, observed

    async def search(self, watch) -> list[Offer]:
        if watch.kind != "discovery" or not watch.query:
            raise ValueError("Amazon-Suche erfordert einen Suchbegriff")
        results = {}
        candidates = set()
        for term in dict.fromkeys([watch.query, *watch.synonyms]):
            remaining = _MAX_SEARCH - len(candidates)
            if not remaining:
                break
            rows, observed = await self._rows(term, remaining)
            for row in rows:
                if not isinstance(row, dict) or row.get("sponsored") is True:
                    continue
                try:
                    asin = _asin(row.get("asin"))
                except ValueError:
                    continue
                if asin in candidates:
                    continue
                candidates.add(asin)
                # A search grid has no verified seller, variant or shipping.
                if watch.gtin or watch.model or watch.variant or canonical_gtin(term):
                    continue
                try:
                    offer = Offer(
                        source=self.id, merchant="Amazon.de", product_id=asin, name=_text(row.get("title"))[:300],
                        url=f"https://www.amazon.de/dp/{asin}", price_cents=_eu_price(row),
                        shipping_cents=None, availability="unknown", checked_at=observed, offer_type="reference",
                    )
                except ValueError:
                    continue
                if matches_product_term(offer, term):
                    results[asin] = offer
        if (watch.gtin or watch.model or watch.variant or canonical_gtin(watch.query)) and candidates:
            raise ValueError("Für bestätigte Amazon-Produktmerkmale bitte den direkten Produktlink verwenden")
        return list(results.values())

    async def lookup(self, watch) -> list[Offer]:
        if watch.kind != "exact":
            raise ValueError("Amazon-Abfrage erfordert eine exakte Produktüberwachung")
        asin = _amazon_asin(watch.url) if watch.url else None
        if watch.product_id:
            product_id = _asin(watch.product_id)
            if asin and product_id != asin:
                raise ValueError("Amazon-Link und ASIN widersprechen sich")
            asin = product_id
        if not asin:
            raise ValueError("Für exakte Amazon-Überwachung bitte einen Produktlink oder eine ASIN angeben")
        offer = await self._details(asin)
        if not _verified_identity(watch, offer):
            raise ValueError("ReefAPI kann die gewünschte Amazon-Produktidentität nicht bestätigen")
        return [offer]
