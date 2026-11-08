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
                "meta": {"api": "amazon", "endpoint": action.split("/v1/")[1],
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
