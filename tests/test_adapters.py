"""Synthetic adapter fixtures; no fixture is presented as a live offer."""

import asyncio
from decimal import Decimal

import pytest

from dealchecker.adapters import build_registry, source_catalog
from dealchecker.adapters.apple import AppleAdapter
from dealchecker.adapters.feed import FeedAdapter
from dealchecker.models import WatchInput


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get_json(self, url, *, allowed_hosts=None):
        self.calls.append((url, allowed_hosts))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def apple_item(track_id=100, name="Monument Valley", price=3.99):
    return {
        "trackId": track_id,
        "trackName": name,
        "price": price,
        "currency": "EUR",
        "kind": "software",
        "trackViewUrl": f"https://apps.apple.com/de/app/monument-valley/id{track_id}?uo=4",
        "artistName": "Studio",
        "primaryGenreName": "Spiele",
    }


@pytest.mark.asyncio
async def test_apple_discovery_queries_synonyms_and_deduplicates():
    client = FakeClient([
        {"resultCount": 1, "results": [apple_item()]},
        {"resultCount": 2, "results": [apple_item(), apple_item(101, "Other", 0)]},
    ])
    adapter = AppleAdapter(client)
    watch = WatchInput(name="Games", kind="discovery", query="Monument", synonyms=["Valley"])
    offers = await adapter.search(watch)
    assert {o.product_id for o in offers} == {"100", "101"}
    assert offers[0].price_cents == 399
    assert offers[0].shipping_cents == 0
    assert offers[0].availability == "in_stock"
    assert offers[0].manufacturer == "Studio"
    assert len(client.calls) == 2
    assert all(hosts == {"itunes.apple.com"} for _, hosts in client.calls)
    assert "country=de" in client.calls[0][0]


@pytest.mark.asyncio
async def test_apple_cache_single_flight_preserves_observation_time():
    client = FakeClient([{"resultCount": 1, "results": [apple_item()]}])
    adapter = AppleAdapter(client)
    watch = WatchInput(name="Games", kind="discovery", query="Monument")
    first, second = await asyncio.gather(adapter.search(watch), adapter.search(watch))
    assert len(client.calls) == 1
    assert first[0].checked_at == second[0].checked_at
    assert (await adapter.search(watch))[0].checked_at == first[0].checked_at


def test_watch_input_rejects_more_than_five_synonyms():
    with pytest.raises(ValueError, match="synonyms"):
        WatchInput(name="Games", kind="discovery", query="one", synonyms=["two", "three", "four", "five", "six", "seven"])


@pytest.mark.asyncio
async def test_apple_exact_id_and_local_url_parse():
    for kwargs in (
        {"product_id": "100"},
        {"url": "https://apps.apple.com/de/app/monument-valley/id100?uo=4"},
    ):
        client = FakeClient([{"resultCount": 1, "results": [apple_item()]}])
        offers = await AppleAdapter(client).lookup(WatchInput(name="Monument Valley", **kwargs))
        assert len(offers) == 1 and offers[0].product_id == "100"
        assert "/lookup?" in client.calls[0][0]


@pytest.mark.asyncio
async def test_apple_exact_title_requires_single_exact_match():
    client = FakeClient([{"resultCount": 3, "results": [apple_item(), apple_item(101, "Other"), apple_item(102)]}])
    with pytest.raises(ValueError, match="mehrdeutig"):
        await AppleAdapter(client).lookup(WatchInput(name="Monument Valley", query="Monument Valley"))


@pytest.mark.asyncio
async def test_apple_exact_id_absent_is_visible():
    client = FakeClient([{"resultCount": 0, "results": []}])
    with pytest.raises(ValueError, match="nicht verfügbar"):
        await AppleAdapter(client).lookup(WatchInput(name="Monument Valley", product_id="100"))


@pytest.mark.asyncio
async def test_apple_rejects_conflicting_url_and_id():
    watch = WatchInput(name="Monument Valley", product_id="100", url="https://apps.apple.com/de/app/x/id101")
    with pytest.raises(ValueError, match="widersprechen"):
        await AppleAdapter(FakeClient([])).lookup(watch)


@pytest.mark.asyncio
async def test_apple_skips_unpriced_arcade_entry_but_not_bad_price():
    arcade = apple_item(102, "Monument Valley+")
    del arcade["price"]
    client = FakeClient([{"resultCount": 2, "results": [arcade, apple_item()]}])
    offers = await AppleAdapter(client).search(WatchInput(name="Game", kind="discovery", query="Monument"))
    assert [offer.product_id for offer in offers] == ["100"]


@pytest.mark.asyncio
async def test_apple_rejects_unsupported_identifiers_and_bad_prices():
    client = FakeClient([])
    with pytest.raises(ValueError, match="GTIN"):
        await AppleAdapter(client).lookup(WatchInput(name="X", gtin="123"))
    client = FakeClient([{"resultCount": 1, "results": [apple_item(price="3.999")]}])
    with pytest.raises(ValueError, match="Preis"):
        await AppleAdapter(client).search(WatchInput(name="X", kind="discovery", query="x"))


@pytest.mark.asyncio
async def test_apple_rejects_unsafe_result_links():
    item = apple_item()
    item["trackViewUrl"] = "javascript:alert(1)"
    client = FakeClient([{"resultCount": 1, "results": [item]}])
    with pytest.raises(ValueError, match="HTTPS"):
        await AppleAdapter(client).search(WatchInput(name="X", kind="discovery", query="x"))


def feed_item(**updates):
    item = {
        "product_id": "sku-1", "name": "Controller Blau", "url": "https://shop.example/products/sku-1",
        "price_cents": 2999, "shipping_cents": 499, "currency": "EUR", "availability": "in_stock",
        "merchant": "Example", "variant": "Blau", "gtin": "1234567890123", "model": "ABC-1",
        "manufacturer": "Acme", "category": "Gaming",
    }
    item.update(updates)
    return item


@pytest.mark.asyncio
async def test_feed_discovery_exact_identity_and_cached_catalog():
    client = FakeClient([{"offers": [feed_item(), feed_item(product_id="sku-2", name="Keyboard")]}])
    adapter = FeedAdapter(client, {"id": "shop", "name": "Shop", "url": "https://feed.example/catalog.json"})
    discovery = WatchInput(name="Controller", kind="discovery", query="Controller", source_ids=["shop"])
    offers = await adapter.search(discovery)
    assert [o.product_id for o in offers] == ["sku-1"]
    exact = WatchInput(name="Controller Blau", product_id="sku-1", variant="Blau", source_ids=["shop"])
    assert (await adapter.lookup(exact))[0].price_cents == 2999
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_feed_same_product_from_two_merchants_is_allowed():
    client = FakeClient([{"offers": [feed_item(), feed_item(merchant="Other", url="https://other.example/item")]}])
    adapter = FeedAdapter(client, {"id": "shop", "name": "Shop", "url": "https://feed.example/catalog.json"})
    offers = await adapter.search(WatchInput(name="Controller", kind="discovery", query="Controller", source_ids=["shop"]))
    assert {offer.merchant for offer in offers} == {"Example", "Other"}


@pytest.mark.asyncio
async def test_exact_feed_title_preserves_multiple_merchants_but_rejects_variants():
    client = FakeClient([{"offers": [feed_item(), feed_item(merchant="Other", url="https://other.example/item")]}])
    adapter = FeedAdapter(client, {"id": "shop", "name": "Shop", "url": "https://feed.example/catalog.json"})
    watch = WatchInput(name="Controller", query="Controller Blau", source_ids=["shop"])
    assert {o.merchant for o in await adapter.lookup(watch)} == {"Example", "Other"}
    other = FeedAdapter(FakeClient([{"offers": [feed_item(), feed_item(product_id="sku-2", variant="different pack")]}]),
                        {"id": "shop", "name": "Shop", "url": "https://feed.example/catalog.json"})
    with pytest.raises(ValueError, match="mehrdeutig"):
        await other.lookup(watch)


@pytest.mark.asyncio
async def test_feed_rejects_malformed_rows_and_unsafe_links():
    for bad in (feed_item(price_cents="29.99"), feed_item(url="http://localhost/private"), feed_item(availability=None)):
        client = FakeClient([{"offers": [bad]}])
        adapter = FeedAdapter(client, {"id": "shop", "name": "Shop", "url": "https://feed.example/catalog.json"})
        with pytest.raises(ValueError):
            await adapter.search(WatchInput(name="Controller", kind="discovery", query="Controller", source_ids=["shop"]))


@pytest.mark.asyncio
async def test_feed_observed_at_preserved_and_stale_quality():
    client = FakeClient([{"offers": [feed_item(observed_at=100.0)]}])
    adapter = FeedAdapter(client, {"id": "shop", "name": "Shop", "url": "https://feed.example/catalog.json"})
    offers = await adapter.search(WatchInput(name="Controller", kind="discovery", query="Controller", source_ids=["shop"]))
    assert offers[0].checked_at == 100.0
    assert offers[0].quality == "feed_stale"


def test_registry_validates_ids_and_urls():
    client = FakeClient([])
    registry = build_registry(client, [{"id": "shop", "name": "Shop", "url": "https://feed.example/catalog.json"}])
    assert set(registry) == {"apple", "shop"}
    assert all(s["configured"] for s in source_catalog(registry))
    with pytest.raises(ValueError):
        build_registry(client, [{"id": "apple", "name": "Bad", "url": "https://feed.example/catalog.json"}])
    with pytest.raises(ValueError):
        build_registry(client, [{"id": "shop", "name": "Bad", "url": "https://127.0.0.1/private"}])
