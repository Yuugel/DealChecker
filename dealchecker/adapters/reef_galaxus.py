"""Opt-in German Galaxus search and product details through ReefAPI."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from dealchecker.identity import canonical_gtin, matches_product_term
from dealchecker.models import Offer

from .base import Adapter, safe_https_url
from .public_pages import _text
from .reef import ReefGateway, _eu_price, _verified_identity


_HOSTS = ("www.galaxus.de", "galaxus.de")
_MERCHANT = "Galaxus"
_SEARCH = "/galaxus/v1/search"
_PRODUCT = "/galaxus/v1/product"
_PAGE_SIZE = 24
_ID = re.compile(r"[1-9][0-9]{0,14}")
_PRODUCT_PATH = re.compile(r"/de/s[1-9][0-9]*/product/[\w.-]+-([1-9][0-9]{0,14})/?")
_CONDITIONS = {
    "RETAIL": "new",
    "MARKETPLACE": "new",
    "SECOND_HAND": "used",
    "RESALE": "used",
    "REFURBISHED": "refurbished",
}


def _product_id(value: object) -> int:
    if type(value) not in (int, str) or not _ID.fullmatch(str(value)):
        raise ValueError("Ungültige Galaxus-Artikelnummer")
    return int(value)


def _url_product_id(url: str) -> int:
    safe_https_url(url, hosts=set(_HOSTS), no_query=True)
    parsed = urlsplit(url)
    path = unquote(parsed.path, errors="strict")
    match = _PRODUCT_PATH.fullmatch(path)
    if not match or path.count("/") != parsed.path.count("/") or len(url) > 1000:
        raise ValueError("Galaxus-Link ist keine direkte deutsche Produktseite")
    return int(match[1])


def _offer_fields(row: dict) -> dict:
    product_id = _product_id(row.get("product_id"))
    url = row.get("url")
    if _url_product_id(url) != product_id:
        raise ValueError("Galaxus-Link und Artikelnummer widersprechen sich")
    quantity = row.get("min_order_quantity")
    if type(quantity) is not int or quantity != 1:
        raise ValueError("Galaxus-Preis gilt nicht eindeutig für einen einzelnen Artikel")
    if row.get("currency") != "EUR":
        raise ValueError("Galaxus-Angebot ist nicht in EUR ausgezeichnet")
    price = _eu_price(row)
    if price <= 0:
        raise ValueError("Galaxus meldet keinen gültigen Artikelpreis")
    name = _text(row.get("full_name")) or _text(row.get("name"))
    if not name:
        raise ValueError("Galaxus meldet keinen Produktnamen")
    brand = _text(row.get("brand"))
    if brand and name.casefold() != brand.casefold() and not name.casefold().startswith(brand.casefold() + " "):
        name = f"{brand} {name}"
    category = row.get("category")
    return {
        "product_id": str(product_id), "name": name[:300], "url": url, "price_cents": price,
        "manufacturer": brand, "category": _text(category.get("name")) if isinstance(category, dict) else "",
    }


def _selected_variant(product: dict, product_id: int) -> str:
    rows = product.get("variants")
    if rows is None:
        return ""
    if not isinstance(rows, list):
        raise ValueError("Galaxus meldet ungültige Varianten")
    current = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("is_current") is not True:
            continue
        group, value = _text(row.get("group")), _text(row.get("value"))
        if (_product_id(row.get("product_id")) != product_id or _url_product_id(row.get("url")) != product_id
                or row.get("currency", "EUR") != "EUR" or not group or not value or group in current):
            raise ValueError("Galaxus-Variante widerspricht der angefragten Artikelnummer")
        current[group] = value
    return "; ".join(current[group] for group in sorted(current))


def _availability(product: dict) -> str:
    stock = product.get("availability")
    stock = stock if isinstance(stock, dict) else {}
    cart = product.get("can_add_to_cart")
    if cart is False or stock.get("is_future_release") is True:
        return "out_of_stock"
    quantity = stock.get("warehouse_quantity")
    if cart is True and (stock.get("warehouse_more_than_10") is True
                         or (type(quantity) is int and quantity > 0)
                         or stock.get("stock_status") == "OnCentralWarehouseStock"):
        return "in_stock"
    return "unknown"


class GalaxusReefAdapter(Adapter):
    id = "galaxus"
    name = "Galaxus (ReefAPI)"
    description = "Galaxus DE über ReefAPI. Preisreferenzen und einzelne Angebote ohne bestätigten Versand"
    supports_search = True
    url_hosts = _HOSTS
    min_interval_seconds = 1800

    def __init__(self, gateway: ReefGateway):
        self.gateway = gateway

    @property
    def configured(self) -> bool:
        return self.gateway.configured

    async def search(self, watch) -> list[Offer]:
        if not self.configured:
            raise ValueError("ReefAPI-Schlüssel für Galaxus fehlt")
        if watch.kind != "discovery" or not watch.query:
            raise ValueError("Galaxus-Suche erfordert einen Suchbegriff")
        if watch.gtin or watch.model or watch.variant or canonical_gtin(watch.query):
            raise ValueError("Für bestätigte Galaxus-Produktmerkmale bitte den direkten Produktlink verwenden")
        results = {}
        for term in dict.fromkeys([watch.query, *watch.synonyms]):
            if canonical_gtin(term):
                continue
            data, observed = await self.gateway.request(
                _SEARCH, {"query": term, "site": "de", "page_size": _PAGE_SIZE, "lang": "de"},
            )
            if not isinstance(data, dict) or data.get("site") != "de" or data.get("currency") != "EUR":
                raise ValueError("ReefAPI liefert keine Galaxus-Suche für Deutschland in EUR")
            rows = data.get("results")
            if not isinstance(rows, list) or len(rows) > _PAGE_SIZE:
                raise ValueError("ReefAPI liefert keine gültige Galaxus-Ergebnisliste")
            if data.get("fallback_results") is True:
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    offer = Offer(
                        source=self.id, merchant=_MERCHANT, **_offer_fields(row),
                        shipping_cents=None, availability="unknown", checked_at=observed, offer_type="reference",
                    )
                except ValueError:
                    continue
                if matches_product_term(offer, term):
                    results.setdefault(offer.product_id, offer)
                    if len(results) >= _PAGE_SIZE:
                        return list(results.values())
        return list(results.values())

    async def lookup(self, watch) -> list[Offer]:
        if not self.configured:
            raise ValueError("ReefAPI-Schlüssel für Galaxus fehlt")
        if watch.kind != "exact":
            raise ValueError("Galaxus-Abfrage erfordert eine exakte Produktüberwachung")
        from_url = _url_product_id(watch.url) if watch.url else None
        product_id = _product_id(watch.product_id) if watch.product_id else from_url
        if product_id is None:
            raise ValueError("Für Galaxus bitte den direkten Produktlink oder die Artikelnummer verwenden")
        if from_url is not None and from_url != product_id:
            raise ValueError("Galaxus-Link und Artikelnummer widersprechen sich")
        if watch.gtin and not canonical_gtin(watch.gtin):
            raise ValueError("GTIN ist ungültig")

        item, observed = await self.gateway.request(_PRODUCT, {"product_id": product_id, "site": "de", "lang": "de"})
        if (not isinstance(item, dict) or item.get("site") != "de"
                or _product_id(item.get("product_id")) != product_id):
            raise ValueError("ReefAPI-Galaxus-Antwort widerspricht Artikelnummer oder Land")
        gtin = _text(item.get("gtin"))
        if item.get("gtin") not in ("", None) and not canonical_gtin(gtin):
            raise ValueError("Galaxus meldet eine ungültige GTIN")
        offer_type = _text(item.get("offer_type"))
        if offer_type not in _CONDITIONS:
            raise ValueError("Galaxus meldet keine eindeutige Angebotsart")
        seller = item.get("merchant")
        seller = _text(seller.get("name")) if isinstance(seller, dict) else _text(seller)
        # ReefAPI documents RETAIL as an offer sold by Galaxus itself.
        if not seller and offer_type == "RETAIL":
            seller = _MERCHANT
        offer = Offer(
            source=self.id, merchant=_MERCHANT, **_offer_fields(item), seller=seller,
            shipping_cents=None, availability=_availability(item), checked_at=observed,
            variant=_selected_variant(item, product_id), gtin=gtin,
            model=_text(item.get("manufacturer_part_number")), condition=_CONDITIONS[offer_type],
        )
        if not _verified_identity(watch, offer):
            raise ValueError("ReefAPI kann die gewünschte Galaxus-Produktidentität nicht bestätigen")
        return [offer]
