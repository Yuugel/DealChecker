"""German MediaMarkt and Saturn public product offers."""

from __future__ import annotations

import re
from time import monotonic, time
from urllib.parse import urlencode, urljoin, urlsplit

from dealchecker.identity import canonical_gtin
from dealchecker.models import Offer

from .public_pages import PublicShopAdapter, _gtin, _money, _public_price, _shipping, _text, _type, jsonld_documents


_PRODUCT_PATH = re.compile(r"/de/product/_[\w.-]+-[0-9]{5,20}\.html")
_GTIN_FIELDS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14")
_PUBLIC_TIER = "Non-loyalty members"
_PRICE_FIELDS = {"@type", "name", "price", "priceCurrency", "validForMemberTier"}
_OPTIONAL_SERVICES = {"WARRANTY", "WARRANTY_SUBSCRIPTION", "DELIVERY_SERVICE", "DISPOSAL_SERVICE"}


def _sku(url: str) -> str:
    return urlsplit(url).path.rsplit("-", 1)[1].removesuffix(".html")


def _confirmed_gtin(product: dict) -> str:
    for field in _GTIN_FIELDS:
        if field in product and not canonical_gtin(_text(product[field])):
            raise ValueError("Produktseite meldet eine ungültige GTIN")
    return _gtin(product, "")


def _price(offer: dict) -> int:
    base = offer.copy()
    # These add-ons have their own prices and do not replace the standard delivery rate.
    addons = base.pop("addOn", [])
    if not isinstance(addons, list) or any(
        not isinstance(addon, dict) or _type(addon.get("@type")) != "Offer"
        or not isinstance(service := addon.get("itemOffered"), dict)
        or _type(service.get("@type")) != "Service"
        or _text(service.get("serviceType")) not in _OPTIONAL_SERVICES
        for addon in addons
    ):
        raise ValueError("Produktseite meldet einen unklaren Zusatz zum Angebot")
    specs = base.pop("priceSpecification", None)
    price = _public_price(base)
    if price <= 0:
        raise ValueError("Produktseite meldet keinen gültigen Kaufpreis")
    if "priceSpecification" in offer:
        _confirm_price_specification(specs, price)
    return price


def _confirm_price_specification(specs: object, price: int) -> None:
    if isinstance(specs, dict):
        if (not specs.keys() - {"@type", "priceType", "price", "priceCurrency"}
                and _type(specs.get("@type")) == "UnitPriceSpecification"
                and _type(specs.get("priceType")) == "StrikethroughPrice"
                and specs.get("priceCurrency") == "EUR" and _money(specs.get("price")) > price):
            return
        raise ValueError("Produktseite meldet keine eindeutigen regulären Preise")
    if not isinstance(specs, list) or any(not isinstance(spec, dict) for spec in specs):
        raise ValueError("Produktseite meldet keine eindeutigen regulären Preise")
    # When tiers are advertised, the explicit nonmember price must equal the offer price.
    public = [spec for spec in specs if isinstance(tier := spec.get("validForMemberTier"), dict)
              and tier.get("name") == _PUBLIC_TIER]
    if (len(public) != 1 or public[0].keys() - _PRICE_FIELDS
            or public[0]["validForMemberTier"].keys() - {"@type", "name"}
            or _type(public[0].get("@type")) != "UnitPriceSpecification"
            or public[0].get("priceCurrency") != "EUR" or _money(public[0].get("price")) != price):
        raise ValueError("Regulärer Preis und Preis für Nichtmitglieder sind nicht eindeutig")


def _delivery(offer: dict) -> int | None:
    details = offer.get("shippingDetails")
    details = details if isinstance(details, list) else [details]
    normalized = []
    for entry in details:
        if isinstance(entry, dict) and isinstance(destination := entry.get("shippingDestination"), dict):
            country = destination.get("addressCountry")
            if isinstance(country, dict) and country.get("name") == "DE":
                if country.keys() - {"@type", "name"} or _type(country.get("@type", "Country")) != "Country":
                    return None
                entry = entry | {"shippingDestination": destination | {"addressCountry": "DE"}}
        normalized.append(entry)
    return _shipping({"shippingDetails": normalized})


class _MediaSaturnAdapter(PublicShopAdapter):
    product_path = _PRODUCT_PATH

    async def lookup(self, watch) -> list[Offer]:
        if not watch.url and not self.supports_search:
            raise ValueError(f"{self.name} benötigt einen direkten Produktlink")
        if watch.url and watch.product_id and _sku(self._product_url(watch.url)) != watch.product_id:
            raise ValueError("Artikelnummer und Produktlink widersprechen sich")
        return await super().lookup(watch)

    def _variant(self, product: dict, url: str, sku: str, gtin: str) -> str:
        if _type(product.get("@type")) != "ProductGroup":
            return _text(product.get("color"))
        variants = product.get("hasVariant")
        if not isinstance(variants, list):
            raise ValueError("Produktseite liefert keine eindeutige Variante")
        selected = [entry for entry in variants if isinstance(entry, dict) and entry.get("sku") == sku]
        if len(selected) != 1 or _type(selected[0].get("@type")) != "Product":
            raise ValueError("Produktseite liefert keine eindeutige Variante")
        variant = selected[0]
        identity = canonical_gtin(gtin)
        selected_offer = variant.get("offers")
        if (identity is None or canonical_gtin(_confirmed_gtin(variant)) != identity
                or not isinstance(selected_offer, dict) or not isinstance(variant_url := selected_offer.get("url"), str)
                or self._product_url(urljoin(self.origin, variant_url)) != url):
            raise ValueError("Variante widerspricht dem Produktlink oder der GTIN")
        return " · ".join(value for field in ("color", "size") if (value := _text(variant.get(field))))

    def _parse_offer(self, html: str, url: str) -> Offer:
        products = [node["object"] for node in jsonld_documents(html)
                    if _type(node.get("@type")) == "BuyAction" and isinstance(node.get("object"), dict)]
        if len(products) != 1 or _type(products[0].get("@type")) not in {"Product", "ProductGroup"}:
            raise ValueError("Produktseite liefert kein eindeutiges Kaufangebot")
        product = products[0]
        sku = _sku(url)
        if product.get("sku") != sku or self._product_url(product.get("url")) != url:
            raise ValueError("Produktlink widerspricht der Artikelnummer")
        gtin = _confirmed_gtin(product)
        variant = self._variant(product, url, sku, gtin)

        offers = product.get("offers")
        offers = offers if isinstance(offers, list) else [offers]
        if len(offers) != 1 or not isinstance(offers[0], dict) or _type(offers[0].get("@type")) != "Offer":
            raise ValueError("Produktseite liefert kein eindeutiges Händlerangebot")
        offer = offers[0]
        if offer.get("priceCurrency") != "EUR" or self._product_url(offer.get("url")) != url:
            raise ValueError("Produktlink oder Währung widerspricht dem Händlerangebot")
        seller = offer.get("seller")
        seller_name = _text(seller.get("name")) if isinstance(seller, dict) else ""
        if seller is not None and not seller_name:
            raise ValueError("Produktseite liefert einen unklaren Verkäufer")
        name = _text(product.get("name"))
        if not name:
            raise ValueError("Produktseite liefert keinen Produktnamen")
        brand = product.get("brand")
        brand = _text(brand.get("name")) if isinstance(brand, dict) else _text(brand)
        availability = {
            "InStock": "in_stock", "OnlineOnly": "in_stock", "LimitedAvailability": "in_stock",
            "OutOfStock": "out_of_stock", "SoldOut": "out_of_stock", "Discontinued": "out_of_stock",
        }.get(_type(offer.get("availability")), "unknown")
        condition = {"NewCondition": "new", "UsedCondition": "used", "RefurbishedCondition": "refurbished"}.get(
            _type(offer.get("itemCondition")), "unknown",
        )
        return Offer(
            source=self.id, merchant=self.name, seller=seller_name, product_id=sku, name=name, url=url,
            variant=variant, gtin=gtin, model=_text(product.get("mpn")), manufacturer=brand,
            category=_text(product.get("category")), price_cents=_price(offer), shipping_cents=_delivery(offer),
            availability=availability, condition=condition, checked_at=time(),
        )


class MediaMarktAdapter(_MediaSaturnAdapter):
    id = "mediamarkt"
    name = "MediaMarkt"
    description = "Öffentliche deutsche Shop-Suche und direkte Produktangebote"
    url_hosts = ("www.mediamarkt.de",)

    async def _links(self, term: str) -> list[tuple[str, str]]:
        expires, links = self._searches.get(term, (0, None))
        if monotonic() < expires:
            return links
        html = await self.pages.get_page(f"{self.origin}/de/search.html?{urlencode({'query': term})}")
        listing = [node.get("itemListElement") for node in jsonld_documents(html)
                   if _type(node.get("@type")) == "ItemList"]
        if len(listing) != 1 or not isinstance(listing[0], list):
            raise ValueError("MediaMarkt-Suche liefert keine eindeutige Produktliste")
        results = {}
        for entry in listing[0][:100]:
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict) or _type(item.get("@type")) != "Product":
                continue
            try:
                url = self._product_url(item.get("url"))
            except ValueError:
                continue
            if name := _text(item.get("name")):
                results.setdefault(url, name)
        return self._store(self._searches, term, list(results.items()))


class SaturnAdapter(_MediaSaturnAdapter):
    id = "saturn"
    name = "Saturn"
    description = "Einzelne deutsche Shop- und Marktplatzangebote per Produktlink"
    url_hosts = ("www.saturn.de",)
    supports_search = False

    async def search(self, watch) -> list[Offer]:
        raise ValueError("Saturn unterstützt nur die Überwachung per direktem Produktlink")
