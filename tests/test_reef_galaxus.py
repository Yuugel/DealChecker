"""Synthetic German Galaxus responses for the opt-in ReefAPI adapter."""

import pytest

from dealchecker.adapters.reef_galaxus import GalaxusReefAdapter
from dealchecker.models import WatchInput


ID = 12345678
GTIN = "4006381333931"
URL = "https://www.galaxus.de/de/s1/product/beispiel-controller-12345678"
OTHER = "https://www.galaxus.de/de/s1/product/beispiel-controller-12345679"
OBSERVED = 1_794_151_234.0
SEARCH = "/galaxus/v1/search"
PRODUCT = "/galaxus/v1/product"


def row(**values):
    return {
        "product_id": ID, "full_name": "Controller USB", "brand": "Beispiel", "url": URL,
        "currency": "EUR", "price": 30.34, "offer_type": "RETAIL", "min_order_quantity": 1,
        "category": {"name": "Gaming Controller"},
    } | values


def detail(**values):
    return row(
        site="de", gtin=GTIN, manufacturer_part_number="M-1", merchant=None, can_add_to_cart=True,
        availability={"stock_status": "OnCentralWarehouseStock", "warehouse_more_than_10": True},
        variants=[
            {"group": "Farbe", "value": "Grün", "product_id": ID, "is_current": True, "url": URL},
            {"group": "Farbe", "value": "Rosa", "product_id": ID + 1, "is_current": False, "url": OTHER},
        ],
    ) | values


class FakeGateway:
    def __init__(self, responses, configured=True):
        self.responses = responses
        self.configured = configured
        self.calls = []

    async def request(self, endpoint, payload):
        self.calls.append((endpoint, payload))
        return self.responses[endpoint], OBSERVED


def watch(**values):
    return WatchInput(**({"name": "Beispiel", "source_ids": ["galaxus"], "url": URL} | values))


@pytest.mark.asyncio
async def test_search_returns_references_in_one_call_without_detail_fanout():
    gateway = FakeGateway({SEARCH: {"site": "de", "currency": "EUR", "results": [
        row(), row(product_id=ID + 1, url=OTHER, currency="CHF"),
        row(product_id=ID + 1, url=OTHER, full_name="Unpassend", brand="Andere"),
        row(product_id=ID + 1, url=OTHER, min_order_quantity=2),
        row(),
    ]}})
    adapter = GalaxusReefAdapter(gateway)
    [offer] = await adapter.search(watch(kind="discovery", url="", query="Beispiel Controller"))
    assert gateway.calls == [(SEARCH, {"query": "Beispiel Controller", "site": "de", "page_size": 24, "lang": "de"})]
    assert (offer.name, offer.offer_type, offer.price_cents, offer.total_cents, offer.checked_at) == (
        "Beispiel Controller USB", "reference", 3034, None, OBSERVED,
    )
    assert offer.seller == "" and offer.availability == "unknown"


@pytest.mark.asyncio
async def test_provider_fallback_results_do_not_become_matches():
    gateway = FakeGateway({SEARCH: {"site": "de", "currency": "EUR", "results": [row()], "fallback_results": True}})
    adapter = GalaxusReefAdapter(gateway)
    assert await adapter.search(watch(kind="discovery", url="", query="Beispiel Controller")) == []


@pytest.mark.asyncio
async def test_direct_offer_verifies_product_variant_and_german_store_without_inventing_shipping():
    gateway = FakeGateway({PRODUCT: detail()})
    adapter = GalaxusReefAdapter(gateway)
    [offer] = await adapter.lookup(watch(gtin=GTIN, model="m-1", variant="Grün", product_id=str(ID)))
    assert gateway.calls == [(PRODUCT, {"product_id": ID, "site": "de", "lang": "de"})]
    assert (offer.merchant, offer.seller, offer.gtin, offer.model, offer.variant, offer.availability) == (
        "Galaxus", "Galaxus", GTIN, "M-1", "Grün", "in_stock",
    )
    assert (offer.offer_type, offer.price_cents, offer.total_cents, offer.checked_at) == (
        "retail", 3034, None, OBSERVED,
    )


@pytest.mark.asyncio
async def test_marketplace_merchant_and_explicit_out_of_stock():
    gateway = FakeGateway({PRODUCT: detail(
        offer_type="MARKETPLACE", merchant={"name": "Beispielhändler"}, can_add_to_cart=False,
    )})
    [offer] = await GalaxusReefAdapter(gateway).lookup(watch(url="", product_id=str(ID)))
    assert (offer.seller, offer.availability, offer.shipping_cents) == ("Beispielhändler", "out_of_stock", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"product_id": ID + 1}, {"site": "at"}, {"currency": "CHF"}, {"url": OTHER},
    {"gtin": "4006381333932"}, {"min_order_quantity": 2},
    {"variants": [{"group": "Farbe", "value": "Rosa", "product_id": ID + 1, "is_current": True, "url": OTHER}]},
])
async def test_mismatched_detail_is_rejected(fields):
    adapter = GalaxusReefAdapter(FakeGateway({PRODUCT: detail(**fields)}))
    with pytest.raises(ValueError):
        await adapter.lookup(watch())


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"url": "https://www.galaxus.ch/de/s1/product/beispiel-controller-12345678"},
    {"url": URL + "?offerid=1"},
    {"url": "https://www.galaxus.de/de/s1/producttype/beispiel-controller-12345678"},
    {"product_id": str(ID + 1)},
])
async def test_bad_links_and_inconsistent_input_do_not_spend_credits(fields):
    gateway = FakeGateway({})
    with pytest.raises(ValueError):
        await GalaxusReefAdapter(gateway).lookup(watch(**fields))
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_unconfigured_and_unverified_search_identity_do_not_spend_credits():
    gateway = FakeGateway({}, configured=False)
    adapter = GalaxusReefAdapter(gateway)
    assert not adapter.configured
    with pytest.raises(ValueError):
        await adapter.lookup(watch())
    gateway.configured = True
    with pytest.raises(ValueError):
        await adapter.search(watch(kind="discovery", url="", query="Beispiel", gtin=GTIN))
    assert gateway.calls == []
