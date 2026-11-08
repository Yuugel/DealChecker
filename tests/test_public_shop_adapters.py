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


@pytest.mark.asyncio
async def test_discovery_uses_ean_search_and_verifies_product_gtin():
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=4006381333931":
            '<a href="/Example/Radio-100/html/product/123456" class="productBox">Radio</a>'
            '<a href="/Example/Different/html/product/234567" class="productBox">Another radio</a>',
        ALTERNATE_PRODUCT: product_page(ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456, gtin_field="gtin8"),
        ALTERNATE + "/Example/Different/html/product/234567":
            product_page(ALTERNATE + "/Example/Different/html/product/234567", "ALTERNATE GmbH", sku=234567),
    })
    watch = WatchInput(name=EXAMPLE, kind="discovery", query=GTIN, source_ids=["alternate"])
    offer, = await AlternateAdapter(pages).search(watch)
    assert offer.gtin == GTIN


@pytest.mark.asyncio
async def test_model_discovery_verifies_mpn_even_if_not_in_search_result_title():
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=XH-Z019":
            '<a href="/Example/Radio-100/html/product/123456" class="productBox">Radio</a>',
        ALTERNATE_PRODUCT: product_page(
            ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456,
        ).replace('"RADIO-100"', '"XH-Z019"'),
    })
    watch = WatchInput(name=EXAMPLE, kind="discovery", query="XH-Z019", source_ids=["alternate"])
    offer, = await AlternateAdapter(pages).search(watch)
    assert offer.model == "XH-Z019"


@pytest.mark.asyncio
async def test_conditional_german_shipping_rate_stays_unknown():
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE_PRODUCT: product_page(
            ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456,
            shipping={"shippingDestination": {"addressCountry": "DE"},
                      "shippingRate": {"currency": "EUR", "value": 0},
                      "eligibleTransactionVolume": {"minPrice": 99, "priceCurrency": "EUR"}},
        ),
    })
    watch = WatchInput(name=EXAMPLE, source_ids=["alternate"], url=ALTERNATE_PRODUCT)
    offer, = await AlternateAdapter(pages).lookup(watch)
    assert offer.shipping_cents is None


@pytest.mark.asyncio
async def test_robots_crawl_delay_applies_to_normal_page_requests():
    from time import monotonic

    robots_url = ALTERNATE + "/robots.txt"
    urls = [ALTERNATE + "/public/one", ALTERNATE + "/public/two"]

    class TimestampedPages(LocalPages):
        def __init__(self, pages):
            super().__init__(pages)
            self.timestamps = []

        async def get_text(self, url, **kwargs):
            self.timestamps.append(monotonic())
            return await super().get_text(url, **kwargs)

    pages = TimestampedPages({robots_url: ROBOTS + "Crawl-delay: 0.02\n", **dict.fromkeys(urls, "ok")})
    client = PublicPageClient(pages, {"www.alternate.de"})
    for url in urls:
        assert await client.get_page(url) == "ok"
    assert all(second - first >= 0.02 for first, second in zip(pages.timestamps, pages.timestamps[1:]))


@pytest.mark.asyncio
async def test_model_discovery_excludes_accessory_titles_and_unverified_models():
    accessory = ALTERNATE + "/Example/Case-for-XH-Z019/html/product/123457"
    unverified = ALTERNATE + "/Example/Bundle-for-XH-Z019/html/product/123458"
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=XH-Z019": (
            '<a href="/Example/Case-for-XH-Z019/html/product/123457" class="productBox">Case</a>'
            '<a href="/Example/Bundle-for-XH-Z019/html/product/123458" class="productBox">Bundle</a>'
            '<a href="/Example/Radio-100/html/product/123456" class="productBox">Radio</a>'
        ),
        accessory: product_page(accessory, "ALTERNATE GmbH", sku=123457)
            .replace(EXAMPLE, "Protective case for XH-Z019").replace("RADIO-100", "CASE-Z019"),
        unverified: product_page(unverified, "ALTERNATE GmbH", sku=123458)
            .replace(EXAMPLE, "Bundle for XH-Z019").replace("RADIO-100", ""),
        ALTERNATE_PRODUCT: product_page(ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456)
            .replace("RADIO-100", "XH-Z019"),
    })
    watch = WatchInput(name=EXAMPLE, kind="discovery", query="XH-Z019", source_ids=["alternate"])
    offer, = await AlternateAdapter(pages).search(watch)
    assert offer.url == ALTERNATE_PRODUCT


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", [
    {"validForMemberTier": {"name": "Member"}},
    {"eligibleCustomerType": "Business"},
    {"priceSpecification": {"price": "29.90", "priceCurrency": "EUR"}},
    {"eligibleQuantity": {"minValue": 2}},
    {"priceValidUntil": "2000-01-01"},
    {"validFrom": "2099-01-01"},
    {"validThrough": "not-a-date"},
])
async def test_direct_link_rejects_conditional_or_invalid_price(condition):
    pages = LocalPages({
        BERRY + "/robots.txt": ROBOTS,
        BERRY_PRODUCT: product_page(BERRY_PRODUCT, "BerryBase", sku="RAD-1",
                                    offers=lambda offer: [{**offer, **condition}]),
    })
    watch = WatchInput(name=EXAMPLE, source_ids=["berrybase"], url=BERRY_PRODUCT)
    with pytest.raises(ValueError, match="(?i)preis"):
        await BerryBaseAdapter(pages).lookup(watch)


@pytest.mark.asyncio
async def test_direct_link_keeps_current_unconditional_price_with_validity_date():
    pages = LocalPages({
        BERRY + "/robots.txt": ROBOTS,
        BERRY_PRODUCT: product_page(BERRY_PRODUCT, "BerryBase", sku="RAD-1",
                                    offers=lambda offer: [{**offer, "priceValidUntil": "2099-01-01"}]),
    })
    watch = WatchInput(name=EXAMPLE, source_ids=["berrybase"], url=BERRY_PRODUCT)
    offer, = await BerryBaseAdapter(pages).lookup(watch)
    assert offer.price_cents == 1990


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", [
    ValueError("Quelle meldet HTTP 404; keine Weiterleitung verfolgt"),
    ValueError("Quelle meldet HTTP 410; keine Weiterleitung verfolgt"),
    "<html><h1>Temporarily missing product markup</h1></html>",
    product_page(ALTERNATE + "/Example/Bad/html/product/234567",
                 "ALTERNATE GmbH", sku=999999),
])
async def test_discovery_preserves_good_offer_when_one_detail_is_bad(broken, caplog):
    bad_url = ALTERNATE + "/Example/Bad/html/product/234567"
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=Example+Radio":
            f'<a class="productBox" title="Example Radio" href="{bad_url}">Old Radio</a>'
            f'<a class="productBox" title="Example Radio" href="{ALTERNATE_PRODUCT}">Current Radio</a>',
        bad_url: broken,
        ALTERNATE_PRODUCT: product_page(
            ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456,
            shipping={"shippingDestination": {"addressCountry": "DE"},
                      "shippingRate": {"currency": "EUR", "value": "7.99"}},
        ),
    })
    watch = WatchInput(name=EXAMPLE, kind="discovery", query=EXAMPLE, source_ids=["alternate"])
    found = await AlternateAdapter(pages).search(watch)
    assert len(found) == 1 and found[0].url == ALTERNATE_PRODUCT
    assert getattr(found, "skipped_pages", 0) == 1
    assert pages.calls == [ALTERNATE + "/robots.txt",
                           ALTERNATE + "/listing.xhtml?q=Example+Radio",
                           bad_url, ALTERNATE_PRODUCT]
    assert "übersprungen" in caplog.text


@pytest.mark.asyncio
async def test_partial_discovery_records_warning_and_alert_without_source_backoff(tmp_path):
    from dealchecker.db import Database
    from dealchecker.service import Engine

    bad_url = ALTERNATE + "/Example/Bad/html/product/234567"
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=Example+Radio":
            f'<a class="productBox" title="Example Radio" href="{bad_url}">Old Radio</a>'
            f'<a class="productBox" title="Example Radio" href="{ALTERNATE_PRODUCT}">Current Radio</a>',
        bad_url: ValueError("Quelle meldet HTTP 404; keine Weiterleitung verfolgt"),
        ALTERNATE_PRODUCT: product_page(
            ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456,
            shipping={"shippingDestination": {"addressCountry": "DE"},
                      "shippingRate": {"currency": "EUR", "value": "7.99"}},
        ),
    })
    db = Database(tmp_path / "partial.db")
    saved = db.create_watch(WatchInput(
        name=EXAMPLE, kind="discovery", query=EXAMPLE, source_ids=["alternate"],
        target_cents=3000,
    ))
    engine = Engine(db, {"alternate": AlternateAdapter(pages)})
    result = await engine.check_watch(saved["id"])
    assert result["status"] == "partial" and result["offers"] == 1
    assert "1 Produktdetailseite(n) ausgelassen" in result["error"]
    assert len(db.list_events()) == 1
    assert db.list_offers(saved["id"])[0]["total_cents"] == 2789
    schedule = db.source_schedules(saved["id"])["alternate"]
    assert schedule["failure_count"] == 0 and schedule["last_error"] is None
    assert db.list_checks()[0]["status"] == "partial"
    await engine.close()
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 503])
async def test_hostwide_block_never_continues_after_failed_product(status):
    bad_url = ALTERNATE + "/Example/Bad/html/product/234567"
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=Example+Radio":
            f'<a class="productBox" title="Example Radio" href="{bad_url}">Old Radio</a>'
            f'<a class="productBox" title="Example Radio" href="{ALTERNATE_PRODUCT}">Current Radio</a>',
        bad_url: ValueError(f"Quelle meldet HTTP {status}; keine Weiterleitung verfolgt"),
        ALTERNATE_PRODUCT: product_page(ALTERNATE_PRODUCT, "ALTERNATE GmbH", sku=123456),
    })
    with pytest.raises(ValueError, match=f"HTTP {status}"):
        await AlternateAdapter(pages).search(
            WatchInput(name=EXAMPLE, kind="discovery", query=EXAMPLE, source_ids=["alternate"]))
    assert ALTERNATE_PRODUCT not in pages.calls


@pytest.mark.asyncio
async def test_exact_missing_page_still_fails_and_all_invalid_discovery_is_not_success():
    bad_url = ALTERNATE + "/Example/Bad/html/product/234567"
    pages = LocalPages({
        ALTERNATE + "/robots.txt": ROBOTS,
        ALTERNATE + "/listing.xhtml?q=Example+Radio":
            f'<a class="productBox" title="Example Radio" href="{bad_url}">Gone Radio</a>',
        bad_url: ValueError("Quelle meldet HTTP 404; keine Weiterleitung verfolgt"),
    })
    adapter = AlternateAdapter(pages)
    with pytest.raises(ValueError, match="nicht mehr verfügbar"):
        await adapter.lookup(WatchInput(name=EXAMPLE, source_ids=["alternate"], url=bad_url))
    with pytest.raises(ValueError, match="kein gültiger Treffer"):
        await adapter.search(WatchInput(name=EXAMPLE, kind="discovery",
                                        query=EXAMPLE, source_ids=["alternate"]))
