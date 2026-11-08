"""Read an exact public Lidl DE product page."""

from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from html.parser import HTMLParser
from time import monotonic, time
from urllib.parse import urlsplit

from dealchecker.identity import canonical_gtin
from dealchecker.models import Offer, WatchInput

from .base import Adapter, safe_https_url
from .public_pages import PublicPageClient, _money, _text, _type, jsonld_documents


_HOST = "www.lidl.de"
_PATH = re.compile(r"/p/[a-z0-9-]+/p([1-9][0-9]{8})/?")
_MAIN_PRODUCT_SCRIPT = re.compile(r"\s*var unified_datalayer_product\s*=\s*(\{.*\})\s*;?\s*", re.DOTALL)
_AVAILABILITY = {"InStock": "in_stock", "OnlineOnly": "in_stock",
                 "OutOfStock": "out_of_stock", "SoldOut": "out_of_stock"}
_LAYER_AVAILABILITY = {"in_stock": "available", "out_of_stock": "not_available"}
_CONDITION = {"NewCondition": "new", "UsedCondition": "used", "RefurbishedCondition": "refurbished"}
_SELLER = "Lidl"


class _MainProductMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts: list[str] = []
        self.javascript = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.javascript = dict(attrs).get("type") == "text/javascript"

    def handle_endtag(self, tag):
        if tag == "script":
            self.javascript = False

    def handle_data(self, data):
        if self.javascript and (match := _MAIN_PRODUCT_SCRIPT.fullmatch(data)):
            self.scripts.append(match.group(1))


def _product_id(url: str) -> str:
    safe_https_url(url, hosts={_HOST}, no_query=True)
    parts = urlsplit(url)
    match = _PATH.fullmatch(parts.path)
    if not match or parts.netloc != _HOST:
        raise ValueError("Lidl benötigt einen direkten Produktlink ohne Varianten- oder Suchparameter")
    return match.group(1)


def _product_gtin(product: dict) -> str:
    codes = {}
    for key in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14"):
        if key not in product:
            continue
        value = product[key]
        values = value if isinstance(value, list) else [value]
        for code in values:
            normalized = canonical_gtin(code) if isinstance(code, str) else None
            if not normalized:
                raise ValueError("Lidl meldet eine ungültige GTIN")
            codes[normalized] = code
    if len(codes) > 1:
        raise ValueError("Lidl meldet mehrere GTIN für diesen Produktlink")
    return next(iter(codes.values()), "")


def _shipping(html: str, product_id: str, name: str, price: int, availability: str) -> int | None:
    # The published page exposes regular home delivery separately from XL and freight charges.
    if availability not in _LAYER_AVAILABILITY:
        return None
    markup = _MainProductMarkup()
    markup.feed(html)
    markup.close()
    if len(markup.scripts) != 1:
        return None
    try:
        layer = json.loads(markup.scripts[0])
        if not isinstance(layer, dict):
            return None
        if (layer.get("id") != product_id or layer.get("type") != "p_pdp"
                or layer.get("status") != "main-product" or layer.get("tradeType") != "online"
                or layer.get("currency") != "EUR" or _text(layer.get("name")) != name
                or layer.get("variantId") is not None or layer.get("parentId") is not None
                or _money(layer.get("price")) != price
                or layer.get("availability") != _LAYER_AVAILABILITY[availability]
                or "shippingCostHomeDeliveryXlCharge" not in layer
                or layer["shippingCostHomeDeliveryXlCharge"] is not None
                or _money(layer.get("forwardingSurcharge")) != 0):
            return None
        return _money(layer.get("shippingCostHomeDeliveryCharge"))
    except (ValueError, RecursionError):
        return None


def _parse_offer(html: str, product_id: str) -> Offer:
    products = [node for node in jsonld_documents(html) if _type(node.get("@type")) == "Product"]
    if len(products) != 1:
        raise ValueError("Lidl liefert kein eindeutiges Produkt")
    product = products[0]
    canonical_url = product.get("url")
    if (product.get("sku") != product_id or _product_id(canonical_url) != product_id
            or product.get("isVariantOf") or product.get("hasVariant")):
        raise ValueError("Lidl-Artikelnummer oder Variante widerspricht dem Produktlink")

    offers = product.get("offers")
    offers = offers if isinstance(offers, list) else [offers]
    if len(offers) != 1 or not isinstance(offers[0], dict) or _type(offers[0].get("@type")) != "Offer":
        raise ValueError("Lidl liefert kein eindeutiges Händlerangebot")
    offer = offers[0]
    if (offer.get("priceCurrency") != "EUR" or _product_id(offer.get("url")) != product_id
            or offer.get("validForMemberTier") or offer.get("eligibleCustomerType")
            or offer.get("priceSpecification")):
        raise ValueError("Lidl liefert keinen eindeutigen öffentlichen EUR-Preis für den Artikel")
    seller = offer.get("seller")
    verified_seller = ""
    if seller is not None:
        seller_name = _text(seller.get("name")) if isinstance(seller, dict) else _text(seller)
        if seller_name.casefold() not in {"lidl", "lidl digital deutschland gmbh & co. kg"}:
            raise ValueError("Lidl meldet einen anderen Verkäufer")
        verified_seller = _SELLER

    name = _text(product.get("name"))
    if not name:
        raise ValueError("Lidl liefert keinen Produktnamen")
    brand = product.get("brand")
    brand = _text(brand.get("name")) if isinstance(brand, dict) else _text(brand)
    price = _money(offer.get("price"))
    if price <= 0:
        raise ValueError("Lidl liefert keinen gültigen Kaufpreis")
    availability = _AVAILABILITY.get(_type(offer.get("availability")), "unknown")
    return Offer(
        source="lidl", merchant=_SELLER, seller=verified_seller, product_id=product_id, name=name,
        url=canonical_url, gtin=_product_gtin(product), manufacturer=brand,
        model=_text(product.get("model")) or _text(product.get("mpn")),
        variant=_text(product.get("color")), category=_text(product.get("category")),
        price_cents=price, shipping_cents=_shipping(html, product_id, name, price, availability),
        availability=availability, condition=_CONDITION.get(_type(offer.get("itemCondition")), "unknown"),
        checked_at=time(),
    )


class LidlAdapter(Adapter):
    id = "lidl"
    name = "Lidl DE"
    description = "Einzelne Lidl-Onlineartikel per direktem Produktlink"
    supports_search = False
    url_hosts = (_HOST,)
    min_interval_seconds = 1800

    def __init__(self, client):
        self.pages = PublicPageClient(client, self.url_hosts)
        self._offers: OrderedDict[str, tuple[float, Offer]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def search(self, watch: WatchInput) -> list[Offer]:
        raise ValueError("Lidl unterstützt nur die Überwachung per direktem Produktlink")

    async def lookup(self, watch: WatchInput) -> list[Offer]:
        if watch.kind != "exact" or not watch.url:
            raise ValueError("Lidl benötigt einen direkten Produktlink")
        product_id = _product_id(watch.url)
        if watch.product_id and watch.product_id != product_id:
            raise ValueError("Lidl-Artikelnummer und Produktlink widersprechen sich")
        expected_gtin = canonical_gtin(watch.gtin) if watch.gtin else None
        if watch.gtin and not expected_gtin:
            raise ValueError("GTIN ist ungültig")
        async with self._lock:
            cached = self._offers.get(product_id)
            if cached and monotonic() < cached[0]:
                offer = cached[1]
            else:
                offer = _parse_offer(await self.pages.get_page(watch.url), product_id)
                self._offers[product_id] = (monotonic() + self.min_interval_seconds, offer)
            self._offers.move_to_end(product_id)
            if len(self._offers) > 96:
                self._offers.popitem(last=False)
        if ((expected_gtin and canonical_gtin(offer.gtin) != expected_gtin)
                or (watch.model and watch.model.casefold() != offer.model.casefold())
                or (watch.variant and watch.variant.casefold() != offer.variant.casefold())):
            raise ValueError("Lidl-Produktidentität widerspricht der Überwachung")
        return [offer]
