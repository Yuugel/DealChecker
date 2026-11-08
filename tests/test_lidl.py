"""Synthetic Lidl HTML examples for exact URL and offer validation."""

import json

import pytest

from dealchecker.adapters.lidl import LidlAdapter
from dealchecker.models import WatchInput


URL = "https://www.lidl.de/p/beispiel-mixer/p100000001"
OTHER_URL = "https://www.lidl.de/p/beispiel-mixer/p100000002"


class FakeHttp:
    def __init__(self, html):
        self.html = html
        self.calls = []

    async def get_text(self, url, *, allowed_hosts, max_body):
        assert allowed_hosts == {"www.lidl.de"}
        assert 0 < max_body <= 2 * 1024 * 1024
        self.calls.append(url)
        return "User-agent: *\nDisallow: *search?q=*\n" if url.endswith("/robots.txt") else self.html


def watch(**fields):
    return WatchInput(**({"name": "Mixer", "source_ids": ["lidl"], "url": URL} | fields))


def page(*, product_fields=None, offer_fields=None, layer_fields=None, extra_offers=()):
    product = {
        "@type": "Product", "sku": "100000001", "name": "Beispiel-Mixer", "url": URL,
        "gtin13": ["4006381333931"], "brand": {"name": "Beispiel"},
    } | (product_fields or {})
    offer = {
        "@type": "Offer", "price": 43.99, "priceCurrency": "EUR", "availability": "OnlineOnly",
        "itemCondition": "NewCondition", "url": URL,
    } | (offer_fields or {})
    product["offers"] = [offer, *extra_offers]
    layer = {
        "id": "100000001", "type": "p_pdp", "status": "main-product", "tradeType": "online",
        "name": "Beispiel-Mixer", "currency": "EUR", "price": 43.99, "availability": "available",
        "shippingCostHomeDeliveryCharge": 5.95, "shippingCostHomeDeliveryXlCharge": None,
        "forwardingSurcharge": 0, "variantId": None, "parentId": None,
    } | (layer_fields or {})
    return (
        f'<script type="application/ld+json">{json.dumps(product)}</script>'
        f'<script type="text/javascript">var unified_datalayer_product = {json.dumps(layer)}</script>'
    )


@pytest.mark.asyncio
async def test_direct_offer_and_de_shipping():
    client = FakeHttp(page(product_fields={"mpn": "M-1"}))
    adapter = LidlAdapter(client)
    [offer] = await adapter.lookup(watch(gtin="4006381333931", model="m-1", product_id="100000001"))

    assert adapter.supports_search is False
    assert adapter.min_interval_seconds == 1800
    assert client.calls == ["https://www.lidl.de/robots.txt", URL]
    assert (offer.source, offer.seller, offer.gtin, offer.condition, offer.availability) == (
        "lidl", "Lidl", "4006381333931", "new", "in_stock",
    )
    assert (offer.price_cents, offer.shipping_cents, offer.total_cents) == (4399, 595, 4994)


@pytest.mark.asyncio
async def test_out_of_stock_is_explicit():
    adapter = LidlAdapter(FakeHttp(page(
        offer_fields={"availability": "https://schema.org/OutOfStock"},
        layer_fields={"availability": "not_available"},
    )))
    [offer] = await adapter.lookup(watch())
    assert (offer.availability, offer.price_cents, offer.shipping_cents) == ("out_of_stock", 4399, 595)


@pytest.mark.asyncio
@pytest.mark.parametrize("layer_fields", [
    {"shippingCostHomeDeliveryXlCharge": 29.95},
    {"forwardingSurcharge": 34.95},
    {"shippingCostHomeDeliveryCharge": None},
    {"price": 39.99},
    {"availability": "not_available"},
    {"id": "100000002"},
])
async def test_ambiguous_shipping_has_no_delivered_total(layer_fields):
    [offer] = await LidlAdapter(FakeHttp(page(layer_fields=layer_fields))).lookup(watch())
    assert offer.shipping_cents is None
    assert offer.total_cents is None


@pytest.mark.asyncio
async def test_comment_is_not_published_shipping_data():
    markup = page().replace('<script type="text/javascript">', '<!-- <script type="text/javascript">')
    [offer] = await LidlAdapter(FakeHttp(markup + " -->")).lookup(watch())
    assert offer.shipping_cents is None


@pytest.mark.asyncio
async def test_repeated_checks_reuse_recent_observation():
    client = FakeHttp(page())
    adapter = LidlAdapter(client)
    first = await adapter.lookup(watch())
    second = await adapter.lookup(watch())
    assert first == second
    assert client.calls == ["https://www.lidl.de/robots.txt", URL]


@pytest.mark.asyncio
@pytest.mark.parametrize("html", [
    page(product_fields={"sku": "100000001001"}),
    page(product_fields={"url": OTHER_URL}),
    page(offer_fields={"url": OTHER_URL}),
    page(offer_fields={"priceCurrency": "USD"}),
    page(offer_fields={"price": "43.999"}),
    page(offer_fields={"@type": "AggregateOffer"}),
    page(product_fields={"gtin13": ["4006381333931", "4052916875346"]}),
    page(extra_offers=[{"@type": "Offer", "price": 39.99}]),
    page(offer_fields={"validForMemberTier": {"name": "Lidl Plus"}}),
    page(offer_fields={"seller": {"name": "Beispielhändler"}}),
])
async def test_unsafe_identity_or_offer_is_rejected(html):
    with pytest.raises(ValueError):
        await LidlAdapter(FakeHttp(html)).lookup(watch())


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"url": URL + "?variant=1"},
    {"url": URL + "001"},
    {"url": ""},
    {"url": "https://lidl.de/p/beispiel-mixer/p100000001"},
    {"url": "https://www.lidl.de/q/search?q=mixer"},
    {"product_id": "100000002"},
])
async def test_links_are_validated_before_any_request(fields):
    client = FakeHttp(page())
    with pytest.raises(ValueError):
        await LidlAdapter(client).lookup(watch(query="Mixer", **fields))
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"gtin": "4052916875346"}, {"model": "M-2"}, {"variant": "weiß"},
])
async def test_optional_identity_must_be_source_reported(fields):
    with pytest.raises(ValueError):
        await LidlAdapter(FakeHttp(page())).lookup(watch(**fields))


@pytest.mark.asyncio
async def test_no_keyword_search_request():
    client = FakeHttp(page())
    with pytest.raises(ValueError, match="Produktlink"):
        await LidlAdapter(client).search(watch(kind="discovery", url="", query="Mixer"))
    assert client.calls == []
