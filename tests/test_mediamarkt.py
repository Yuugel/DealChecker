"""Synthetic MediaMarkt and Saturn public-page structures."""

import json
from urllib.parse import urlsplit

import pytest

from dealchecker.adapters.mediamarkt import MediaMarktAdapter, SaturnAdapter
from dealchecker.models import WatchInput


SKU = "1000010"
GTIN = "4006381333931"
PATH = "/de/product/_beispiel-controller-1000010.html"
MEDIA = "https://www.mediamarkt.de"
SATURN = "https://www.saturn.de"


def markup(node):
    return f'<script type="application/ld+json">{json.dumps(node)}</script>'


def page(url, *, product_fields=None, offer_fields=None):
    product = {
        "@type": "Product", "sku": SKU, "gtin13": GTIN, "name": "Beispiel Controller",
        "brand": {"@type": "Brand", "name": "Beispiel"}, "url": url,
    } | (product_fields or {})
    offer = {
        "@type": "Offer", "url": url, "priceCurrency": "EUR", "price": 42.6,
        "itemCondition": "https://schema.org/NewCondition",
        "availability": "https://schema.org/InStock",
        "seller": {"@type": "Organization", "name": "Beispielhändler"},
        "shippingDetails": {
            "@type": "OfferShippingDetails",
            "shippingDestination": {"@type": "DefinedRegion", "addressCountry": {"@type": "Country", "name": "DE"}},
            "shippingRate": {"@type": "MonetaryAmount", "value": 0, "currency": "EUR"},
        },
    } | (offer_fields or {})
    product.setdefault("offers", [offer])
    return markup({"@context": "https://schema.org/", "@type": "BuyAction", "object": product})


def selected_variant(url, **overrides):
    return {"@type": "Product", "sku": SKU, "gtin13": GTIN, "color": "Rosa",
            "offers": {"@type": "Offer", "url": urlsplit(url).path}} | overrides


def member_price(name, price):
    return {"@type": "UnitPriceSpecification", "price": price, "priceCurrency": "EUR",
            "validForMemberTier": {"@type": "MemberProgramTier", "name": name}}


class FakeHttp:
    def __init__(self, pages, robots="User-agent: *\nDisallow: /intern/\n"):
        self.pages = pages
        self.robots = robots
        self.calls = []

    async def get_text(self, url, *, allowed_hosts, max_body):
        assert allowed_hosts == {urlsplit(url).hostname}
        assert max_body <= 2 * 1024 * 1024
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots
        return self.pages[url]


def exact(adapter, url, **values):
    return WatchInput(**({"name": "Beispiel", "url": url, "source_ids": [adapter.id]} | values))


@pytest.mark.asyncio
@pytest.mark.parametrize("cls,origin,merchant", [
    (MediaMarktAdapter, MEDIA, "MediaMarkt"), (SaturnAdapter, SATURN, "Saturn"),
])
async def test_direct_product_confirmed_seller_and_shipping(cls, origin, merchant):
    url = origin + PATH
    http = FakeHttp({url: page(url)})
    adapter = cls(http)
    [offer] = await adapter.lookup(exact(adapter, url, gtin=GTIN, product_id=SKU))
    assert (offer.merchant, offer.seller, offer.gtin, offer.condition, offer.availability) == (
        merchant, "Beispielhändler", GTIN, "new", "in_stock",
    )
    assert (offer.price_cents, offer.shipping_cents, offer.total_cents) == (4260, 0, 4260)
    assert http.calls == [origin + "/robots.txt", url]


@pytest.mark.asyncio
async def test_selected_product_group_requires_matching_variant_and_public_price():
    url = MEDIA + PATH
    html = page(url, product_fields={"@type": "ProductGroup", "hasVariant": [selected_variant(url)]},
                offer_fields={"seller": None, "priceSpecification": [
                    member_price("Non-loyalty members", "42.60"), member_price("Loyalty member", "39.99"),
                ], "addOn": [{"@type": "Offer", "itemOffered": {"@type": "Service", "serviceType": "WARRANTY"}}]})
    adapter = MediaMarktAdapter(FakeHttp({url: html}))
    [offer] = await adapter.lookup(exact(adapter, url, variant="Rosa"))
    assert (offer.variant, offer.seller, offer.price_cents) == ("Rosa", "", 4260)


@pytest.mark.asyncio
async def test_optional_appliance_services_do_not_replace_regular_delivery():
    url = MEDIA + PATH
    html = page(url, offer_fields={"addOn": [
        {"@type": "Offer", "price": 30, "itemOffered": {"@type": "Service", "serviceType": "DELIVERY_SERVICE"}},
        {"@type": "Offer", "price": 0, "itemOffered": {"@type": "Service", "serviceType": "DISPOSAL_SERVICE"}},
        {"@type": "Offer", "price": 2.49, "itemOffered": {"@type": "Service", "serviceType": "WARRANTY_SUBSCRIPTION"}},
    ]})
    adapter = MediaMarktAdapter(FakeHttp({url: html}))
    [offer] = await adapter.lookup(exact(adapter, url))
    assert (offer.price_cents, offer.shipping_cents, offer.total_cents) == (4260, 0, 4260)


@pytest.mark.asyncio
async def test_marketplace_strikethrough_price_is_not_used_as_offer():
    url = MEDIA + PATH
    html = page(url, offer_fields={"priceSpecification": {
        "@type": "UnitPriceSpecification", "priceType": "https://schema.org/StrikethroughPrice",
        "price": 49, "priceCurrency": "EUR",
    }})
    adapter = MediaMarktAdapter(FakeHttp({url: html}))
    [offer] = await adapter.lookup(exact(adapter, url))
    assert offer.price_cents == 4260


@pytest.mark.asyncio
@pytest.mark.parametrize("product_fields,offer_fields", [
    ({"sku": "1000011"}, {}),
    ({"gtin13": "4006381333932"}, {}),
    ({"@type": "ProductGroup", "hasVariant": [selected_variant(MEDIA + PATH, gtin13="4052916875346")]}, {}),
    ({"offers": [{"@type": "AggregateOffer", "lowPrice": 1}]}, {}),
    ({}, {"url": SATURN + PATH}),
    ({}, {"priceCurrency": "USD"}),
    ({}, {"priceSpecification": [member_price("Loyalty member", 42.6)]}),
    ({}, {"priceSpecification": [member_price("Non-loyalty members", 43.6)]}),
    ({}, {"priceSpecification": member_price("Loyalty member", 42.6)}),
    ({}, {"addOn": [{"@type": "Offer", "itemOffered": {"@type": "Product"}}]}),
    ({}, {"validForMemberTier": {"name": "Loyalty member"}}),
])
async def test_wrong_identity_or_nonpublic_price_is_rejected(product_fields, offer_fields):
    url = MEDIA + PATH
    adapter = MediaMarktAdapter(FakeHttp({url: page(url, product_fields=product_fields, offer_fields=offer_fields)}))
    with pytest.raises(ValueError):
        await adapter.lookup(exact(adapter, url))


@pytest.mark.asyncio
async def test_location_dependent_shipping_stays_unknown():
    url = MEDIA + PATH
    html = page(url, offer_fields={"shippingDetails": {
        "shippingDestination": {"addressCountry": {"name": "DE"}, "postalCode": "12345"},
        "shippingRate": {"value": 0, "currency": "EUR"},
    }})
    adapter = MediaMarktAdapter(FakeHttp({url: html}))
    [offer] = await adapter.lookup(exact(adapter, url))
    assert offer.shipping_cents is None
    assert offer.total_cents is None


@pytest.mark.asyncio
async def test_media_search_reads_products_not_the_listing_price():
    url = MEDIA + PATH
    listing = markup({"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": {"@type": "Product", "name": "Beispiel Controller", "url": url,
                                      "offers": {"@type": "Offer", "price": 1, "priceCurrency": "EUR"}}},
    ]})
    search_url = MEDIA + "/de/search.html?query=Beispiel+Controller"
    http = FakeHttp({search_url: listing, url: page(url)})
    adapter = MediaMarktAdapter(http)
    [offer] = await adapter.search(WatchInput(name="Beispiel", kind="discovery", query="Beispiel Controller"))
    assert adapter.supports_search
    assert offer.total_cents == 4260
    assert http.calls == [MEDIA + "/robots.txt", search_url, url]


@pytest.mark.asyncio
async def test_saturn_refuses_search_and_url_less_exact_watches():
    http = FakeHttp({})
    adapter = SaturnAdapter(http)
    assert not adapter.supports_search
    with pytest.raises(ValueError, match="Produktlink"):
        await adapter.search(WatchInput(name="Beispiel", kind="discovery", query="Beispiel"))
    with pytest.raises(ValueError, match="Produktlink"):
        await adapter.lookup(WatchInput(name="Beispiel", gtin=GTIN))
    assert http.calls == []


@pytest.mark.asyncio
async def test_direct_product_respects_live_robots():
    url = MEDIA + PATH
    http = FakeHttp({url: page(url)}, robots="User-agent: *\nDisallow: /de/product/\n")
    adapter = MediaMarktAdapter(http)
    with pytest.raises(ValueError, match="robots.txt sperrt"):
        await adapter.lookup(exact(adapter, url))
    assert http.calls == [MEDIA + "/robots.txt"]
