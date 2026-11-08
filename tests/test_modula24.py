"""Synthetic Modula24 fixtures; tests never contact the merchant."""

import asyncio

import pytest

from dealchecker.adapters.modula24 import Modula24Adapter
from dealchecker.models import WatchInput


class FakeClient:
    def __init__(self, responses, quotes=()):
        self.responses = list(responses)
        self.quotes = list(quotes)
        self.calls = []

    async def get_json(self, url, *, allowed_hosts=None, max_body=None):
        self.calls.append((url, allowed_hosts, max_body))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def post_json_response(self, url, payload, *, allowed_hosts=None):
        self.calls.append((url, allowed_hosts, payload))
        return self.quotes.pop(0)


def product(*, price="2.50"):
    return {"product": {"handle": "rohr-ohne-bohrung", "title": "Rohr ohne Bohrung", "vendor": "Modula24",
                        "product_type": "Rohre", "options": [{"name": "Länge", "position": 1}],
                        "variants": [
                            {"id": 123, "title": "100 mm", "price": price, "available": True, "option1": "100"},
                            {"id": 456, "title": "125 mm", "price": price, "available": True, "option1": "125"}]}}


def quote():
    return {"country": "DE", "currency": "EUR", "shipping_options": [{"id": "dhl", "cost": "5.22"}]}


def row(variant_id="123", **updates):
    value = {
        "id": f"M24-{variant_id}", "variant_id": variant_id,
        "title": "Rohr ohne Bohrung - 100 mm", "link": "https://modula24.com/products/rohr-ohne-bohrung",
        "price": "2.50 EUR", "availability": "in_stock", "brand": "Modula24",
        "product_type": "Rohre", "options": {"Länge": "100"},
        "shipping": [{"countries": ["DE", "AT"], "price": "5.22 EUR", "min_days": 1, "max_days": 2}],
    }
    value.update(updates)
    return value


def feed(*rows):
    return {"generated": "2026-11-08T12:34:48Z", "language": "de", "currency": "EUR", "items": list(rows)}


def discovery(query="Rohr"):
    return WatchInput(name="Möbelteile", kind="discovery", query=query, source_ids=["modula24"])


@pytest.mark.asyncio
async def test_discovery_maps_real_feed_shape_with_variant_and_de_shipping():
    client = FakeClient([feed(row(), row("456", title="Kugel", options={}, product_type="Kugeln", price="3.50 EUR"))])
    adapter = Modula24Adapter(client)
    offers = await adapter.search(discovery())
    assert len(offers) == 1
    offer = offers[0]
    assert (offer.source, offer.merchant, offer.product_id) == ("modula24", "Modula24", "123")
    assert (offer.price_cents, offer.shipping_cents, offer.total_cents) == (250, 522, 772)
    assert offer.variant == "Länge: 100"
    assert offer.manufacturer == "Modula24" and offer.category == "Rohre"
    assert offer.url == "https://modula24.com/products/rohr-ohne-bohrung?variant=123"
    assert offer.availability == "in_stock"
    assert client.calls == [("https://modula24.com/feeds/products.de.json", {"modula24.com"}, 4 * 1024 * 1024)]


@pytest.mark.asyncio
async def test_feed_variant_url_is_preserved_when_identity_matches():
    link = "https://modula24.com/products/rohr-ohne-bohrung?variant=123"
    offer = (await Modula24Adapter(FakeClient([feed(row(link=link))])).search(discovery()))[0]
    assert offer.url == link


@pytest.mark.asyncio
async def test_exact_id_and_url_disambiguate_variants_and_conflicts_fail():
    rows = [row(), row("456", title="Rohr ohne Bohrung - 125 mm", options={"Länge": "125"})]
    adapter = Modula24Adapter(FakeClient([feed(*rows), product()], [quote()]))
    result = await adapter.lookup(WatchInput(name="Rohr", product_id="456", source_ids=["modula24"]))
    assert [offer.product_id for offer in result] == ["456"]
    result = await adapter.lookup(WatchInput(name="Rohr", url=result[0].url, source_ids=["modula24"]))
    assert [offer.product_id for offer in result] == ["456"]
    with pytest.raises(ValueError, match="widersprechen"):
        await adapter.lookup(WatchInput(name="Rohr", product_id="123", url=result[0].url, source_ids=["modula24"]))


@pytest.mark.asyncio
async def test_cache_singleflight_preserves_observation_and_refreshes_after_expiry():
    client = FakeClient([feed(row()), product(price="1.50")], [quote()])
    adapter = Modula24Adapter(client)
    first, second = await asyncio.gather(adapter.search(discovery()), adapter.search(discovery()))
    assert len(client.calls) == 1
    assert first[0].checked_at == second[0].checked_at
    assert (await adapter.search(discovery()))[0].checked_at == first[0].checked_at
    adapter._expires = 0
    refreshed = await adapter.lookup(WatchInput(name="Rohr", product_id="123", source_ids=["modula24"]))
    assert refreshed[0].price_cents == 150 and len(client.calls) == 3
    assert refreshed[0].checked_at >= first[0].checked_at


@pytest.mark.asyncio
async def test_unknown_shipping_is_not_zero_and_out_of_stock_is_preserved():
    adapter = Modula24Adapter(FakeClient([feed(row(shipping=[], availability="out_of_stock"))]))
    offer = (await adapter.search(discovery()))[0]
    assert offer.shipping_cents is None and offer.total_cents is None
    assert offer.availability == "out_of_stock"


@pytest.mark.asyncio
async def test_documented_free_shipping_remains_zero():
    shipping = [{"countries": ["DE"], "price": "0.00 EUR"}]
    offer = (await Modula24Adapter(FakeClient([feed(row(shipping=shipping))])).search(discovery()))[0]
    assert offer.shipping_cents == 0 and offer.total_cents == offer.price_cents


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    row(price="1.234 EUR"), row(price="1.50 USD"), row(price="-1.50 EUR"),
    row(link="https://evil.example/products/rohr"), row(link="http://modula24.com/products/rohr"),
    row(id="M24-999"), row(variant_id="not-an-id"), row(availability="unknown"),
    row(link="https://modula24.com/products/rohr-ohne-bohrung?variant=999"),
    row(link="https://modula24.com/products/rohr-ohne-bohrung?tracking=1"),
    row(shipping=[{"countries": ["DE"], "price": "bad"}]),
    row(shipping=[{"countries": ["DE"], "price": "5.22 EUR"}, {"countries": ["DE"], "price": "4.00 EUR"}]),
])
async def test_bad_offer_fails_closed(bad):
    adapter = Modula24Adapter(FakeClient([feed(bad)]))
    with pytest.raises(ValueError):
        await adapter.search(discovery())


@pytest.mark.asyncio
async def test_duplicate_variant_or_invalid_catalog_fails_closed():
    for payload in [feed(row(), row()), {"items": []}, feed(*[row(str(i)) for i in range(5001)])]:
        adapter = Modula24Adapter(FakeClient([payload]))
        with pytest.raises(ValueError):
            await adapter.search(discovery())


@pytest.mark.asyncio
async def test_empty_catalog_and_network_failure_are_distinct():
    assert await Modula24Adapter(FakeClient([feed()])).search(discovery()) == []
    with pytest.raises(ValueError, match="Netzwerk"):
        await Modula24Adapter(FakeClient([ValueError("Netzwerkfehler")])).search(discovery())
