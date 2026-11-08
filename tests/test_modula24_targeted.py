"""Targeted merchant checks use synthetic product and shipping responses."""

import asyncio

import pytest

from dealchecker.adapters.modula24 import Modula24Adapter
from dealchecker.models import WatchInput


URL = "https://modula24.com/products/test-rohr"


def product(*, price="2.50", available=True, variants=None, handle="test-rohr", options=None,
            currency=None):
    value = {"product": {"id": 7, "handle": handle, "title": "Test Rohr", "vendor": "Modula24",
                         "product_type": "Rohre", "variants": variants if variants is not None else [
                             {"id": 123, "title": "100", "price": price, "available": available,
                              "option1": "100", "option2": None, "option3": None}],
                         "options": options or [{"name": "Länge", "position": 1, "values": "100"}]}}
    if currency is not None:
        value["product"]["currency"] = currency
    return value


def shipping(*, cost="5.22", options=None, currency="EUR"):
    return {"country": "DE", "currency": currency,
            "shipping_options": options if options is not None else [
                {"id": "fourpx", "cost": "3.73"}, {"id": "dhl", "cost": cost}]}


def feed():
    return {"generated": "2026-11-08T12:34:48Z", "language": "de", "currency": "EUR", "items": [
        {"id": "M24-123", "variant_id": "123", "title": "Test Rohr - 100", "link": URL,
         "price": "2.50 EUR", "availability": "in_stock", "brand": "Modula24", "product_type": "Rohre",
         "options": {"Länge": "100"}, "shipping": [{"countries": ["DE"], "price": "5.22 EUR"}]}]}


class Client:
    def __init__(self, products=(), quotes=(), feeds=()):
        self.products, self.quotes, self.feeds = list(products), list(quotes), list(feeds)
        self.calls = []

    async def get_json(self, url, **kwargs):
        self.calls.append(("GET", url))
        item = (self.feeds if "/feeds/" in url else self.products).pop(0)
        if isinstance(item, Exception): raise item
        return item

    async def post_json_response(self, url, payload, **kwargs):
        self.calls.append(("POST", url, payload))
        item = self.quotes.pop(0)
        if isinstance(item, Exception): raise item
        return item


def watch(*, product_id="123", url=f"{URL}?variant=123", **kwargs):
    return WatchInput(name="Test Rohr", source_ids=["modula24"], product_id=product_id, url=url, **kwargs)


@pytest.mark.asyncio
async def test_known_url_targets_product_and_standard_dhl_quote_without_feed():
    client = Client(products=[product()], quotes=[shipping()])
    adapter = Modula24Adapter(client)
    first, second = await asyncio.gather(adapter.lookup(watch()), adapter.lookup(watch()))
    assert first == second
    offer = first[0]
    assert (offer.product_id, offer.variant, offer.price_cents, offer.shipping_cents) == ("123", "Länge: 100", 250, 522)
    assert offer.url == f"{URL}?variant=123"
    assert [call[0] for call in client.calls] == ["GET", "POST"]
    assert client.calls[0][1] == URL + ".json"
    assert client.calls[1][2] == {"country": "DE", "items": [{"id": "123", "qty": 1}], "currency": "EUR"}


@pytest.mark.asyncio
async def test_target_cache_preserves_timestamp_and_refreshes_price_after_expiry():
    client = Client(products=[product(), product(price="1.50")], quotes=[shipping(), shipping(cost="0.00")])
    adapter = Modula24Adapter(client)
    first = (await adapter.lookup(watch()))[0]
    assert (await adapter.lookup(watch()))[0].checked_at == first.checked_at
    adapter._target_expires.clear()
    second = (await adapter.lookup(watch()))[0]
    assert (second.price_cents, second.shipping_cents) == (150, 0)
    assert second.checked_at >= first.checked_at
    assert [call[0] for call in client.calls] == ["GET", "POST", "GET", "POST"]


@pytest.mark.asyncio
async def test_id_only_bootstraps_feed_once_then_refreshes_target_without_feed():
    client = Client(feeds=[feed()], products=[product(), product(price="1.50")],
                    quotes=[shipping(), shipping()])
    adapter = Modula24Adapter(client)
    first = (await adapter.lookup(watch(url="")))[0]
    adapter._target_expires.clear()
    second = (await adapter.lookup(watch(url="")))[0]
    assert (first.price_cents, second.price_cents) == (250, 150)
    assert [call[0] for call in client.calls] == ["GET", "GET", "POST", "GET", "POST"]
    assert sum("/feeds/" in call[1] for call in client.calls) == 1


@pytest.mark.asyncio
async def test_url_without_variant_requires_unique_product_variant():
    variants = [{"id": 123, "title": "100", "price": "2.50", "available": True, "option1": "100"},
                {"id": 456, "title": "125", "price": "3.50", "available": True, "option1": "125"}]
    adapter = Modula24Adapter(Client(products=[product(variants=variants)]))
    with pytest.raises(ValueError, match="mehrere Varianten"):
        await adapter.lookup(watch(url=URL, product_id=""))


@pytest.mark.asyncio
async def test_variant_identity_and_explicit_variant_constraint():
    variants = [{"id": 123, "title": "100", "price": "2.50", "available": True, "option1": "100"},
                {"id": 456, "title": "125", "price": "3.50", "available": True, "option1": "125"}]
    client = Client(products=[product(variants=variants)], quotes=[shipping()])
    adapter = Modula24Adapter(client)
    offer = (await adapter.lookup(watch(product_id="456", url=f"{URL}?variant=456", variant="Länge: 125")))[0]
    assert (offer.product_id, offer.price_cents, offer.variant) == ("456", 350, "Länge: 125")
    with pytest.raises(ValueError, match="widersprechen"):
        await adapter.lookup(watch(product_id="123", url=f"{URL}?variant=456"))
    with pytest.raises(ValueError, match="Variante"):
        await adapter.lookup(watch(product_id="456", url=f"{URL}?variant=456", variant="Länge: 100"))


@pytest.mark.asyncio
async def test_absent_dhl_and_out_of_stock_keep_total_unknown():
    client = Client(products=[product(available=False), product()], quotes=[shipping(options=[{"id": "fourpx", "cost": "3.73"}])])
    adapter = Modula24Adapter(client)
    first = (await adapter.lookup(watch()))[0]
    assert first.availability == "out_of_stock" and first.shipping_cents is None
    adapter._target_expires.clear()
    second = (await adapter.lookup(watch()))[0]
    assert second.shipping_cents is None and second.total_cents is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_product,bad_quote", [
    (product(price="2.500"), shipping()), (product(currency="USD"), shipping()),
    (product(handle="wrong"), shipping()), (product(variants=[]), shipping()),
    (product(), shipping(currency="USD")), (product(), shipping(cost="invalid")),
    (product(), shipping(options=[{"id": "dhl", "cost": "5.22"}, {"id": "dhl", "cost": "1.00"}])),
    (product(), shipping(options=["bad"])),
])
async def test_bad_targeted_data_fails_closed(bad_product, bad_quote):
    client = Client(products=[bad_product], quotes=[bad_quote])
    with pytest.raises(ValueError):
        await Modula24Adapter(client).lookup(watch())


@pytest.mark.asyncio
async def test_failed_refresh_does_not_retimestamp_or_cache_stale_offer():
    client = Client(products=[product(), ValueError("Netzwerk"), product(price="1.50")],
                    quotes=[shipping(), shipping()])
    adapter = Modula24Adapter(client)
    original = (await adapter.lookup(watch()))[0]
    adapter._target_expires.clear()
    with pytest.raises(ValueError, match="Netzwerk"):
        await adapter.lookup(watch())
    refreshed = (await adapter.lookup(watch()))[0]
    assert refreshed.price_cents == 150 and refreshed.checked_at >= original.checked_at
