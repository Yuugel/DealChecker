"""Synthetic ReefAPI fixtures. Public demonstration checks are separate and opt-in."""
from types import SimpleNamespace

import pytest

from dealchecker.adapters.reef import AmazonReefAdapter, ReefGateway
from dealchecker.models import WatchInput


ASIN = "B0A1234567"
SECOND = "B0A1234568"
AMAZON_LINK = f"https://www.amazon.de/dp/{ASIN}"
SEARCH = "/amazon/v1/search"
DETAIL = "/amazon/v1/product/detail"


class Settings:
    def __init__(self, key="synthetic-test-key", budget=20):
        self.secret = key
        self.budget = budget
        self.generation = 0
        self.used = 0
        self.delivery_postcode = ""

    @property
    def configured(self):
        return bool(self.secret)

    def key(self):
        return self.secret

    def reserve(self, credits):
        if self.used + credits > self.budget:
            raise ValueError("Tageslimit erreicht")
        self.used += credits


class Pages:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def post_json_response(self, url, payload, **kwargs):
        self.calls.append((url, payload, kwargs))
        action = url.removeprefix("https://api.reefapi.com")
        data = self.responses[action]
        return {"ok": True, "data": data,
                "meta": {"api": action.split("/")[1], "endpoint": action.split("/v1/")[1],
                         "mode": "live", "cache_hit": False}}


def product(**changes):
    return {"product": {"asin": ASIN, "title": "Example Gamepad, Mint", "price": 32.42,
                        "currency_code": "EUR", "currency": "€", "in_stock": True, "color": "Mint",
                        "buybox_seller": "Example Seller", "fulfillment": "amazon",
                        "delivery_message": "GRATIS Lieferung für qualifizierte Erstbestellung",
                        "attributes": {"model_number": "4006381333931"}, **changes}}


@pytest.mark.asyncio
async def test_amazon_direct_link_is_canonical_and_prime_text_does_not_prove_free_shipping():
    transport = Pages({DETAIL: product()})
    settings = Settings()
    adapter = AmazonReefAdapter(ReefGateway(transport, settings))
    watch = WatchInput(name="Example Gamepad", url=AMAZON_LINK + "?tag=ignored", source_ids=["amazon"])
    offer, = await adapter.lookup(watch)
    assert (offer.product_id, offer.url, offer.price_cents, offer.seller) == (ASIN, AMAZON_LINK, 3242, "Example Seller")
    assert (offer.shipping_cents, offer.total_cents, offer.gtin, offer.variant) == (None, None, "", "Mint")
    assert offer.availability == "in_stock"
    assert settings.used == 2
    url, payload, kwargs = transport.calls[0]
    assert url == "https://api.reefapi.com" + DETAIL
    assert payload["marketplace"] == "de"
    assert payload["asin"] == ASIN
    assert kwargs["headers"] == {"x-api-key": "synthetic-test-key"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"asin_mismatch": True}, {"served_asin": SECOND}, {"currency_code": "USD"}])
async def test_amazon_refuses_wrong_market_or_child_variant(change):
    adapter = AmazonReefAdapter(ReefGateway(Pages({DETAIL: product(**change)}), Settings()))
    watch = WatchInput(name="Example Gamepad", url=AMAZON_LINK, source_ids=["amazon"])
    with pytest.raises(ValueError):
        await adapter.lookup(watch)


@pytest.mark.asyncio
async def test_amazon_discovery_is_bounded_and_does_not_claim_unverified_offer_details():
    transport = Pages({SEARCH: {"results": [
        {"asin": ASIN, "title": "Example Gamepad, Mint", "price": 32.42, "currency": "€"},
        {"asin": SECOND, "title": "Unrelated kettle", "price": 22, "currency": "€"},
    ]}})
    settings = Settings()
    adapter = AmazonReefAdapter(ReefGateway(transport, settings))
    watch = WatchInput(name="Example Gamepad", query="Example Gamepad", kind="discovery", source_ids=["amazon"])
    offer, = await adapter.search(watch)
    assert (offer.offer_type, offer.seller, offer.shipping_cents, offer.availability) == (
        "reference", "", None, "unknown")
    assert settings.used == 5
    assert transport.calls[0][1]["max_results"] == 5


@pytest.mark.asyncio
async def test_reef_gateway_caches_per_generation_and_reserves_before_network():
    transport = Pages({DETAIL: product()})
    settings = Settings(budget=4)
    gateway = ReefGateway(transport, settings)
    request = {"marketplace": "de", "asin": ASIN}
    data, first = await gateway.request(DETAIL, request)
    data["product"]["price"] = 0
    second, checked_at = await gateway.request(DETAIL, request)
    assert second["product"]["price"] == 32.42
    assert checked_at == first
    assert settings.used == 2
    settings.generation += 1
    await gateway.request(DETAIL, request)
    settings.generation += 1
    with pytest.raises(ValueError, match="Tageslimit"):
        await gateway.request(DETAIL, request)
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_reef_gateway_missing_key_never_makes_requests():
    transport = Pages({})
    gateway = ReefGateway(transport, Settings(key=""))
    with pytest.raises(ValueError, match="Schlüssel"):
        await gateway.request(DETAIL, {"asin": ASIN, "marketplace": "de"})
    assert transport.calls == []


@pytest.mark.asyncio
async def test_reef_gateway_refuses_unapproved_endpoint_before_reserving():
    settings = Settings()
    transport = Pages({})
    gateway = ReefGateway(transport, settings)
    with pytest.raises(ValueError):
        await gateway.request("/other/v1/data", {})
    assert settings.used == 0
    assert transport.calls == []


EBAY_ID = "123456789012"
EBAY_LINK = f"https://www.ebay.de/itm/{EBAY_ID}"
EBAY_SEARCH = "/ebay/v1/search"
EBAY_DETAIL = "/ebay/v1/item_detail"


def listing(**changes):
    return {
        "item_id": EBAY_ID,
        "title": "Example Gamepad",
        "url": EBAY_LINK,
        "price": {"value": 49.9, "currency": "EUR"},
        "condition": "New",
        "in_stock": True,
        "listing_status": "active",
        "buying_format": "buy_it_now",
        "seller": {"username": "example-shop"},
        "shipping": {"rate": 4.95, "currency": "EUR", "country": "DEU"},
        "ship_to": {"country": "DEU", "country_code": "DE", "postal_code": "10115"},
        "shipping_status": "ok",
        "item_specifics": {"Marke": "Example", "Herstellernummer": "EX-001", "EAN": "4006381333931"},
        **changes,
    }


@pytest.mark.asyncio
async def test_ebay_detail_preserves_seller_german_shipping_and_reported_identity():
    from dealchecker.adapters.reef import EbayReefAdapter

    transport = Pages({EBAY_DETAIL: {"item": listing()}})
    settings = Settings()
    settings.delivery_postcode = "10115"
    adapter = EbayReefAdapter(ReefGateway(transport, settings))
    watch = WatchInput(name="Example Gamepad", url=EBAY_LINK, source_ids=["ebay"], gtin="4006381333931")
    offer, = await adapter.lookup(watch)
    assert (offer.seller, offer.shipping_cents, offer.total_cents, offer.gtin) == (
        "example-shop", 495, 5485, "4006381333931")
    assert offer.condition == "new"
    assert transport.calls[0][1]["domain"] == "de"


@pytest.mark.asyncio
async def test_ebay_auction_is_not_returned_as_buy_now_offer():
    from dealchecker.adapters.reef import EbayReefAdapter

    transport = Pages({EBAY_DETAIL: {"item": listing(buying_format="auction", current_bid=49.9)}})
    adapter = EbayReefAdapter(ReefGateway(transport, Settings()))
    watch = WatchInput(name="Example Gamepad", url=EBAY_LINK, source_ids=["ebay"])
    with pytest.raises(ValueError, match="Sofortkauf"):
        await adapter.lookup(watch)


@pytest.mark.asyncio
async def test_ebay_search_excludes_auction_and_marks_unconfirmed_seller_as_reference():
    from dealchecker.adapters.reef import EbayReefAdapter

    transport = Pages({EBAY_SEARCH: {"results": [
        listing(buying_format_code="buy_it_now"),
        listing(item_id="123456789013", url="https://www.ebay.de/itm/123456789013",
                buying_format_code="auction"),
    ]}})
    adapter = EbayReefAdapter(ReefGateway(transport, Settings()))
    watch = WatchInput(name="Example Gamepad", kind="discovery", query="Example Gamepad", source_ids=["ebay"])
    offer, = await adapter.search(watch)
    assert (offer.product_id, offer.offer_type, offer.seller, offer.total_cents) == (EBAY_ID, "reference", "", None)


@pytest.mark.asyncio
async def test_reef_gateway_does_not_serve_cache_past_maximum_provider_observation_age(monkeypatch):
    from datetime import datetime, timezone
    from dealchecker.adapters import reef

    class AgedPages(Pages):
        async def post_json_response(self, *args, **kwargs):
            result = await super().post_json_response(*args, **kwargs)
            result["meta"]["fetched_at"] = datetime.fromtimestamp(clock.now - 599, timezone.utc).isoformat()
            return result

    clock = SimpleNamespace(now=1794150000, tick=1000)
    monkeypatch.setattr(reef, "time", lambda: clock.now)
    monkeypatch.setattr(reef, "monotonic", lambda: clock.tick)
    transport = AgedPages({DETAIL: product()})
    gateway = ReefGateway(transport, Settings())
    await gateway.request(DETAIL, {"asin": ASIN})
    clock.now += 2
    clock.tick += 2
    await gateway.request(DETAIL, {"asin": ASIN})
    assert len(transport.calls) == 2



@pytest.mark.asyncio
@pytest.mark.parametrize("postcode", ["", "20095"])
async def test_ebay_does_not_use_shipping_for_default_or_wrong_postcode(postcode):
    from dealchecker.adapters.reef import EbayReefAdapter

    transport = Pages({EBAY_DETAIL: {"item": listing()}})
    settings = Settings()
    settings.delivery_postcode = postcode
    adapter = EbayReefAdapter(ReefGateway(transport, settings))
    watch = WatchInput(name="Example Gamepad", url=EBAY_LINK, source_ids=["ebay"])
    offer, = await adapter.lookup(watch)
    assert offer.shipping_cents is None
    assert transport.calls[0][1].get("ship_to_postal", "") == postcode


@pytest.mark.asyncio
async def test_reef_gateway_rejects_sample_even_with_fresh_timestamp():
    from datetime import datetime, timezone

    class SamplePages(Pages):
        async def post_json_response(self, *args, **kwargs):
            result = await super().post_json_response(*args, **kwargs)
            result["meta"].update(mode="sample", fetched_at=datetime.now(timezone.utc).isoformat())
            return result

    gateway = ReefGateway(SamplePages({DETAIL: product()}), Settings())
    with pytest.raises(ValueError, match="Live"):
        await gateway.request(DETAIL, {"asin": ASIN})


@pytest.mark.asyncio
@pytest.mark.parametrize("variant_data", [
    {"variations": [{"color": "Blue", "price": 29.90}, {"color": "Red", "price": 59.90}]},
    {"variation_count": 2},
])
async def test_ebay_detail_does_not_use_listing_price_for_an_unverified_variant(variant_data):
    from dealchecker.adapters.reef import EbayReefAdapter

    item = listing(price={"value": 29.9, "currency": "EUR"}, **variant_data)
    adapter = EbayReefAdapter(ReefGateway(Pages({EBAY_DETAIL: {"item": item}}), Settings()))
    watch = WatchInput(name="Example Gamepad", url=EBAY_LINK, source_ids=["ebay"])
    with pytest.raises(ValueError, match="Variante"):
        await adapter.lookup(watch)


@pytest.mark.asyncio
@pytest.mark.parametrize("auction_evidence", [
    {"auction": True}, {"type": "Auction"}, {"bid_count": 3}, {"bids": "2"}, {"time_left": "Endet in 3 Stunden"},
])
async def test_ebay_quantity_does_not_override_auction_signals(auction_evidence):
    from dealchecker.adapters.reef import EbayReefAdapter

    item = listing(buying_format="", quantity_available=3, **auction_evidence)
    adapter = EbayReefAdapter(ReefGateway(Pages({EBAY_DETAIL: {"item": item}}), Settings()))
    watch = WatchInput(name="Example Gamepad", url=EBAY_LINK, source_ids=["ebay"])
    with pytest.raises(ValueError, match="Sofortkauf"):
        await adapter.lookup(watch)


@pytest.mark.parametrize('quantity', [0, -1, '0', True])
async def test_ebay_empty_or_unconfirmed_quantity_never_triggers_purchase_alarm(tmp_path, quantity):
    from dealchecker.adapters.reef import EbayReefAdapter
    from dealchecker.db import Database
    from dealchecker.service import Engine
    item = listing(quantity_available=quantity, in_stock=True, availability='InStock', listing_status='active')
    settings = Settings()
    settings.delivery_postcode = '10115'
    adapter = EbayReefAdapter(ReefGateway(Pages({EBAY_DETAIL: {'item': item}}), settings))
    db = Database(tmp_path / 'stock.sqlite3')
    engine = Engine(db, {'ebay': adapter})
    try:
        watch = db.create_watch(WatchInput(name='Synthetic stock contradiction', url=EBAY_LINK,
                                          source_ids=['ebay'], target_cents=10000))
        assert (await engine.check_watch(watch['id']))['status'] == 'ok'
        assert db.list_offers()[0]['availability'] != 'in_stock'
        assert db.list_events() == []
    finally:
        await engine.close()
        db.close()
