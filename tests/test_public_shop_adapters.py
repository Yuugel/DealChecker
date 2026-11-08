"""Synthetic public HTML fixtures. Network verification is opt-in."""
import json

import pytest

from dealchecker.adapters.alternate import AlternateAdapter
from dealchecker.adapters.berrybase import BerryBaseAdapter
from dealchecker.adapters.public_pages import PublicPageClient
from dealchecker.models import WatchInput


ALTERNATE = "https://www.alternate.de"
BERRY = "https://www.berrybase.de"
ALTERNATE_PRODUCT = ALTERNATE + "/Example/Radio-100/html/product/123456"
BERRY_PRODUCT = BERRY + "/example-radio-100-pink"
GTIN = "4006381333931"
ROBOTS = "User-agent: *\nDisallow: /checkout/\n"
EXAMPLE = "Example Radio 100"


class LocalPages:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def get_text(self, url, *, allowed_hosts, max_body):
        self.calls.append(url)
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return result


def product_page(url, merchant, *, sku, gtin_field="", shipping=None, markup="", offers=None):
    offer = {
        "@type": "Offer", "url": url, "priceCurrency": "EUR", "price": "19.90",
        "availability": "https://schema.org/InStock", "itemCondition": "NewCondition",
        "seller": {"@type": "Organization", "name": merchant},
    }
    if shipping is not None:
        offer["shippingDetails"] = shipping
    product = {
        "@context": "https://schema.org", "@type": "Product", "sku": sku,
        "name": EXAMPLE, "brand": {"name": "Example"}, "mpn": "RADIO-100",
        "offers": [offer] if offers is None else offers(offer),
    }
    if gtin_field:
        product[gtin_field] = GTIN
    return '<script type="application/ld+json">' + json.dumps([product]) + "</script>" + markup


@pytest.mark.asyncio
async def test_robots_applies_specific_group_wildcard_anchor_and_allow_tie():
    robots_url = ALTERNATE + "/robots.txt"
    urls = [ALTERNATE + path for path in ("/public", "/limited/first", "/limited/item", "/limited/item?ok=1")]
    pages = LocalPages({
        robots_url: (
            "User-agent: *\nDisallow: /\n\n"
            "User-agent: DealChecker\nDisallow: /limited/*\n"
            "Disallow: /limited/item$\nAllow: /limited/item$\n"
        ),
        **dict.fromkeys(urls, "content"),
    })
    client = PublicPageClient(pages, {"www.alternate.de"})
    assert await client.get_page(urls[0]) == "content"
    assert await client.get_page(urls[2]) == "content"
    for url in (urls[1], urls[3]):
        with pytest.raises(ValueError, match="robots"):
            await client.get_page(url)
    assert pages.calls == [robots_url, urls[0], urls[2]]


@pytest.mark.asyncio
@pytest.mark.parametrize("robots", ["<html>Security Check</html>", ValueError("HTTP 403")])
async def test_robots_unavailable_or_invalid_blocks_the_page(robots):
    robots_url = BERRY + "/robots.txt"
    pages = LocalPages({robots_url: robots})
    client = PublicPageClient(pages, {"www.berrybase.de"})
    for _ in range(2):
        with pytest.raises(ValueError, match="robots"):
            await client.get_page(BERRY_PRODUCT)
    assert pages.calls == [robots_url]


@pytest.mark.asyncio
async def test_alternate_exact_gtin_returns_verified_offer_and_german_shipping():
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=4006381333931":
            '<a href="/Example/Radio-100/html/product/123456" class="productBox">Radio</a>',
        ALTERNATE_PRODUCT: product_page(
            ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456, gtin_field="gtin8",
            shipping={"shippingDestination": {"addressCountry": "DE"},
                      "shippingRate": {"currency": "EUR", "value": 7.99}},
            markup='<h1><small class="product-name-sub">(Pink)</small></h1>',
        ),
    })
    adapter = AlternateAdapter(pages)
    watch = WatchInput(name=EXAMPLE, source_ids=["alternate"], gtin="0" + GTIN)
    offer, = await adapter.lookup(watch)
    assert (offer.product_id, offer.gtin, offer.variant, offer.merchant) == ("123456", GTIN, "Pink", "ALTERNATE GmbH")
    assert (offer.price_cents, offer.shipping_cents, offer.total_cents, offer.availability) == (1990, 799, 2789, "in_stock")


@pytest.mark.asyncio
async def test_berrybase_search_verifies_listing_and_offer_uses_visible_ean():
    pages = LocalPages({
        BERRY + "/robots.txt": ROBOTS,
        BERRY + "/search?search=Example+Radio+100": (
            '<a class="product-name stretched-link" href="https://www.berrybase.de/example-radio-100-pink"'
            ' title="Example Radio 100 Pink">Radio</a>'
            '<a class="product-name" href="/different" title="Totally unrelated">Other</a>'
        ),
        BERRY_PRODUCT: product_page(
            BERRY_PRODUCT, "BerryBase", sku="RAD-1",
            markup='<table><tr class="properties-row"><th class="properties-label">EAN:</th>'
                   f'<td class="properties-value"><span>{GTIN}</span></td></tr></table>',
        ),
    })
    adapter = BerryBaseAdapter(pages)
    watch = WatchInput(name=EXAMPLE, kind="discovery", query=EXAMPLE, source_ids=["berrybase"])
    offer, = await adapter.search(watch)
    assert (offer.url, offer.gtin, offer.seller, offer.price_cents) == (BERRY_PRODUCT, GTIN, "BerryBase", 1990)
    assert offer.shipping_cents is None
    assert len(pages.calls) == 3


@pytest.mark.asyncio
async def test_product_link_rejects_ambiguous_offers():
    pages = LocalPages({
        BERRY + "/robots.txt": ROBOTS,
        BERRY_PRODUCT: product_page(
            BERRY_PRODUCT, "BerryBase", sku="RAD-1",
            offers=lambda offer: [offer, {**offer, "price": "22.95"}],
        ),
    })
    watch = WatchInput(name=EXAMPLE, source_ids=["berrybase"], url=BERRY_PRODUCT)
    with pytest.raises(ValueError, match="eindeutig"):
        await BerryBaseAdapter(pages).lookup(watch)


@pytest.mark.asyncio
async def test_berrybase_reports_explicit_product_color():
    pages = LocalPages({
        BERRY + "/robots.txt": ROBOTS,
        BERRY_PRODUCT: product_page(
            BERRY_PRODUCT, "BerryBase", sku="RAD-1",
            markup="<ul><li>Farbe: Pink</li><li>Spannung: 5 V</li></ul>",
        ).replace(EXAMPLE, EXAMPLE + ", Pink"),
    })
    watch = WatchInput(name=EXAMPLE, source_ids=["berrybase"], url=BERRY_PRODUCT, variant="Pink")
    offer, = await BerryBaseAdapter(pages).lookup(watch)
    assert offer.variant == "Pink"


@pytest.mark.asyncio
async def test_robots_rejects_url_paths_that_could_normalize_differently():
    robots_url = ALTERNATE + "/robots.txt"
    url = ALTERNATE + "/public/%2e%2e/checkout/private"
    pages = LocalPages({robots_url: ROBOTS, url: "should not be loaded"})
    with pytest.raises(ValueError, match="Pfad"):
        await PublicPageClient(pages, {"www.alternate.de"}).get_page(url)
    assert url not in pages.calls


@pytest.mark.asyncio
async def test_page_403_pauses_further_requests_to_that_host():
    first, second = BERRY + "/first-product", BERRY + "/second-product"
    pages = LocalPages({BERRY + "/robots.txt": ROBOTS, first: ValueError("Quelle meldet HTTP 403"), second: "page"})
    client = PublicPageClient(pages, {"www.berrybase.de"})
    with pytest.raises(ValueError, match="403"):
        await client.get_page(first)
    with pytest.raises(ValueError, match="pausiert"):
        await client.get_page(second)
    assert second not in pages.calls
