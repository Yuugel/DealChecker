"""Opt-in ReefAPI retail data for German storefronts."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from html import unescape
import json
import re
from time import monotonic, time
from urllib.parse import parse_qs, unquote, urlsplit

from dealchecker.identity import canonical_gtin, matches_product_term
from dealchecker.models import Offer

from .base import Adapter, safe_https_url
from .public_pages import _money, _text


_HOST = "api.reefapi.com"
_AMAZON_DETAIL = "/amazon/v1/product/detail"
_AMAZON_SEARCH = "/amazon/v1/search"
_EBAY_DETAIL = "/ebay/v1/item_detail"
_EBAY_SEARCH = "/ebay/v1/search"
_COSTS = {
    _AMAZON_DETAIL: 2,
    _EBAY_SEARCH: 1,
    _EBAY_DETAIL: 2,
    '/galaxus/v1/search': 2,
    '/galaxus/v1/product': 2,
}
_MAX_SEARCH = 5
_MAX_CACHE = 48
_CACHE_SECONDS = 600
_MAX_BODY = 512 * 1024
_ASIN = re.compile(r"[A-Z0-9]{10}")
_AMAZON_PATH = re.compile(r"/(?:(?:[^/]+/)?dp|gp/product)/([A-Z0-9]{10})(?:/ref=[^/]+)?/?", re.I)
_EBAY_ID = re.compile(r"[0-9]{12}")
_EBAY_PATH = re.compile(r"/itm/(?:[^/]+/)?([0-9]{12})/?")
_POSTCODE = re.compile(r"[0-9]{5}")


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
            now = time()
            if (cached is not None and monotonic() < cached[0]
                    and now - _CACHE_SECONDS <= cached[2] <= now + 60):
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
            remaining = max(0, min(_CACHE_SECONDS, observed_at + _CACHE_SECONDS - time()))
            self._cache[key] = (monotonic() + remaining, data, observed_at)
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
        if meta.get("mode") != "live":
            raise ValueError("ReefAPI liefert keinen Live-Abruf")
        received = time()
        timestamp = meta.get("fetched_at")
        if timestamp is None and meta.get("cache_hit") is False:
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
        if watch.gtin or watch.model or watch.variant or canonical_gtin(watch.query):
            raise ValueError("Für bestätigte Amazon-Produktmerkmale bitte den direkten Produktlink verwenden")
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
                if canonical_gtin(term):
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


def _ebay_id(value: object) -> str:
    if not isinstance(value, str) or not _EBAY_ID.fullmatch(value):
        raise ValueError("Ungültige eBay-Artikelnummer")
    return value


def _ebay_listing_id(url: str) -> str:
    safe_https_url(url, hosts=set(EbayReefAdapter.url_hosts))
    parsed = urlsplit(url)
    path = unquote(parsed.path, errors="strict")
    if (path.count("/") != parsed.path.count("/") or "\\" in path
            or any(part in {".", ".."} for part in path.split("/"))
            or any(ord(char) < 32 for char in path)
            or not (match := _EBAY_PATH.fullmatch(path))):
        raise ValueError("eBay-Link ist keine direkte deutsche Artikelseite")
    if {key.lower() for key in parse_qs(parsed.query, keep_blank_values=True)} & {"var", "variation", "variationid"}:
        raise ValueError("ReefAPI bestätigt die gewählte eBay-Variante im Direktlink nicht")
    return _ebay_id(match[1])


def _ebay_price(item: dict) -> int:
    price = item.get("price")
    if not isinstance(price, dict) or price.get("currency") != "EUR":
        raise ValueError("eBay-Angebot ist nicht eindeutig in EUR ausgezeichnet")
    raw = _text(price.get("raw")).casefold()
    if re.search(r"\b(?:ab|bis|from|to)\b", raw) or price.get("max") is not None:
        raise ValueError("eBay-Angebot nennt nur einen Mindestpreis oder eine Preisspanne")
    return _money(price.get("value"))


def _ebay_condition(value: object) -> str:
    name = _text(value).casefold()
    if name in {"neu", "new", "brand new"}:
        return "new"
    if name in {"gebraucht", "used", "pre-owned"}:
        return "used"
    return "refurbished" if "refurbished" in name else "unknown"


def _ebay_stock(item: dict) -> str:
    if item.get("listing_status") in {"ended", "sold", "expired"}:
        return "out_of_stock"
    end = item.get("end_time")
    if end is not None:
        try:
            expires = datetime.fromisoformat(end)
            if expires.tzinfo is None:
                return "unknown"
            if expires.timestamp() <= time():
                return "out_of_stock"
        except (TypeError, ValueError, OverflowError, OSError):
            return "unknown"
    availability = _text(item.get("availability")).casefold()
    if availability in {"outofstock", "soldout"} or item.get("in_stock") is False:
        return "out_of_stock"
    if item.get("listing_status") == "active" and (availability == "instock" or item.get("in_stock") is True):
        return "in_stock"
    return "unknown"


def _ebay_shipping(item: dict, postcode: str) -> int | None:
    shipping, destination = item.get("shipping"), item.get("ship_to")
    if (not postcode or not isinstance(shipping, dict) or not isinstance(destination, dict)
            or item.get("shipping_status") != "ok" or shipping.get("currency") != "EUR"
            or shipping.get("country") not in {"DE", "DEU"} or destination.get("country") not in {"DE", "DEU"}
            or destination.get("country_code") != "DE" or destination.get("postal_code") != postcode):
        return None
    try:
        return _money(shipping.get("rate"))
    except ValueError:
        return None


def _ebay_buying_format(item: dict) -> str:
    return _text(item.get("buying_format_code") or item.get("buying_format")).casefold()


_FIXED_PRICE = {"buy_it_now", "fixed_price", "sofort-kaufen", "buy it now"}


class EbayReefAdapter(Adapter):
    id = "ebay"
    name = "eBay (ReefAPI)"
    description = "eBay DE über optionalen ReefAPI-Zugang; Sofortkaufpreise, Versand zur eingestellten PLZ"
    min_interval_seconds = 1800
    supports_search = True
    url_hosts = ("www.ebay.de", "ebay.de")

    def __init__(self, gateway: ReefGateway):
        self.gateway = gateway

    @property
    def configured(self) -> bool:
        return self.gateway.configured

    def _postal_parameters(self) -> tuple[dict, str]:
        postcode = self.gateway.settings.delivery_postcode
        if not postcode:
            return {}, ""
        if not _POSTCODE.fullmatch(postcode):
            raise ValueError("Ungültige deutsche Liefer-PLZ für eBay")
        return {"ship_to_postal": postcode}, postcode

    async def search(self, watch) -> list[Offer]:
        if watch.kind != "discovery" or not watch.query:
            raise ValueError("eBay-Suche erfordert einen Suchbegriff")
        if watch.gtin or watch.model or watch.variant or canonical_gtin(watch.query):
            raise ValueError("Für bestätigte eBay-Produktmerkmale bitte den direkten Artikellink verwenden")
        postal_parameters, _ = self._postal_parameters()
        results = {}
        for term in dict.fromkeys([watch.query, *watch.synonyms]):
            remaining = _MAX_SEARCH - len(results)
            if not remaining:
                break
            data, observed = await self.gateway.request(
                _EBAY_SEARCH, {"query": term, "domain": "de", "max_results": remaining,
                               "buy_it_now": True, **postal_parameters},
            )
            rows = data.get("results") if isinstance(data, dict) else None
            if not isinstance(rows, list) or len(rows) > remaining:
                raise ValueError("ReefAPI-eBay-Suche liefert ein ungültiges Ergebnis")
            for row in rows:
                if not isinstance(row, dict) or _ebay_buying_format(row) not in _FIXED_PRICE:
                    continue
                try:
                    item_id = _ebay_id(row.get("item_id"))
                    if _ebay_listing_id(row.get("url")) != item_id:
                        continue
                    offer = Offer(
                        source=self.id, merchant="eBay.de", product_id=item_id,
                        name=_text(row.get("title"))[:300], url=f"https://www.ebay.de/itm/{item_id}",
                        price_cents=_ebay_price(row), shipping_cents=None, availability="unknown",
                        condition=_ebay_condition(row.get("condition")), checked_at=observed, offer_type="reference",
                    )
                except ValueError:
                    continue
                if matches_product_term(offer, term):
                    results.setdefault(item_id, offer)
        return list(results.values())

    async def lookup(self, watch) -> list[Offer]:
        if watch.kind != "exact":
            raise ValueError("eBay-Abfrage erfordert eine exakte Artikelüberwachung")
        item_id = _ebay_listing_id(watch.url) if watch.url else None
        if watch.product_id:
            product_id = _ebay_id(watch.product_id)
            if item_id and item_id != product_id:
                raise ValueError("eBay-Link und Artikelnummer widersprechen sich")
            item_id = product_id
        if not item_id:
            raise ValueError("Für exakte eBay-Überwachung bitte einen Artikellink oder eine Artikelnummer angeben")
        postal_parameters, postcode = self._postal_parameters()
        data, observed = await self.gateway.request(
            _EBAY_DETAIL, {"item_id": item_id, "domain": "de", "include_description": False, **postal_parameters},
        )
        item = data.get("item") if isinstance(data, dict) else None
        if (not isinstance(item, dict) or item.get("item_id") != item_id
                or _ebay_listing_id(item.get("url")) != item_id):
            raise ValueError("ReefAPI-Angebot widerspricht der angefragten eBay-Artikelnummer")
        buying_format = _ebay_buying_format(item)
        if "auction" in buying_format or "auktion" in buying_format or item.get("current_bid") is not None:
            raise ValueError("eBay-Auktion liefert keinen bestätigten Sofortkaufpreis")
        # eBay limits multi-quantity listings of identical items to fixed-price format.
        quantity = item.get("quantity_available")
        fixed_price = buying_format in _FIXED_PRICE or (type(quantity) is int and quantity > 1)
        specifics = item.get("item_specifics")
        specifics = specifics if isinstance(specifics, dict) else {}
        brand = _text(item.get("brand")) or _text(specifics.get("Marke"))
        model = (_text(item.get("mpn")) or _text(specifics.get("Herstellernummer"))
                 or _text(item.get("model")) or _text(specifics.get("Modell")))
        seller = item.get("seller")
        seller = (_text(seller.get("name")) or _text(seller.get("username"))) if isinstance(seller, dict) else ""
        category = item.get("category_path")
        title = re.sub(r"<wbr\s*/?>", "", _text(item.get("title")), flags=re.I)
        offer = Offer(
            source=self.id, merchant="eBay.de", seller=seller, product_id=item_id,
            name=unescape(title)[:300], url=f"https://www.ebay.de/itm/{item_id}",
            price_cents=_ebay_price(item), shipping_cents=_ebay_shipping(item, postcode) if fixed_price else None,
            checked_at=observed, availability=_ebay_stock(item),
            condition=_ebay_condition(item.get("condition")), offer_type="retail" if fixed_price else "reference",
            gtin=_product_gtin({"ean": specifics.get("EAN"), "gtin": item.get("gtin")}),
            variant=_text(specifics.get("Farbe")), manufacturer=brand,
            model="" if model.casefold() in {"n/a", "nicht zutreffend"} else model,
            category=_text(category[-1]) if isinstance(category, list) and category else "",
        )
        if not _verified_identity(watch, offer):
            raise ValueError("ReefAPI kann die gewünschte eBay-Artikelidentität nicht bestätigen")
        return [offer]
