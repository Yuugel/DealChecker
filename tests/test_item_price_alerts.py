"""Regression coverage for optional article-price alerts without known shipping.

All merchant data and webhook calls in this module are synthetic.
"""
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dealchecker.app import create_app
from dealchecker.config import Config
from dealchecker.db import Database, item_alert_key
from dealchecker.models import Offer, WatchInput
from dealchecker.notifications import DiscordNotifier
from dealchecker.service import Engine, _matches


class Shop:
    id = "shop"
    name = "Synthetic shop"
    description = "Synthetic prices, no network"
    min_interval_seconds = 1800
    supports_search = True

    def __init__(self, *, price=3500, shipping=None, availability="in_stock", offer_type="retail"):
        self.price = price
        self.shipping = shipping
        self.availability = availability
        self.offer_type = offer_type

    async def lookup(self, watch):
        return [Offer(
            source=self.id, merchant=self.name, seller="Test seller",
            product_id="demo-item", name="Example controller",
            url="https://example.com/demo-item",
            price_cents=self.price, shipping_cents=self.shipping,
            availability=self.availability, offer_type=self.offer_type,
            checked_at=time.time(),
        )]

    search = lookup


def watch(**changes):
    return WatchInput(**({
        "name": "Example controller", "source_ids": ["shop"], "product_id": "demo-item",
        "alert_on_item_price": True,
    } | changes))


@pytest.mark.asyncio
async def test_article_price_target_alert_has_null_total_and_deduplicates(tmp_path):
    db = Database(tmp_path / "item-alert.sqlite")
    source = Shop(price=3500)
    engine = Engine(db, {"shop": source})
    wid = db.create_watch(watch(target_cents=3000))["id"]
    try:
        assert (await engine.check_watch(wid))["status"] == "ok"
        assert db.list_events() == []
        source.price = 2900
        assert (await engine.check_watch(wid))["status"] == "ok"
        [event] = db.list_events()
        assert event["price_scope"] == "item"
        assert event["total_cents"] is None
        assert event["price_cents"] == event["alert_cents"] == 2900
        assert event["reason"] == "Artikelpreis: Wunschpreis erreicht"
        assert event["comparison_cents"] == 3500
        assert event["savings_cents"] == 600
        assert db.list_offers()[0]["total_cents"] is None
        assert len(db.history(db.list_offers()[0]["id"])) == 2
        assert (await engine.check_watch(wid))["status"] == "ok"
        assert len(db.list_events()) == 1, "same article-only target must not notify repeatedly"
    finally:
        await engine.close()
        db.close()


@pytest.mark.asyncio
async def test_item_and_total_events_are_distinct_even_when_the_amount_matches(tmp_path):
    db = Database(tmp_path / "separate-bases.sqlite")
    source = Shop(price=2900, shipping=500)
    engine = Engine(db, {"shop": source})
    wid = db.create_watch(watch(target_cents=3100))["id"]
    try:
        await engine.check_watch(wid)
        assert db.list_events() == [], "known delivered total 34 EUR is above the target"
        source.shipping = None
        await engine.check_watch(wid)
        [item_event] = db.list_events()
        assert item_event["price_scope"] == "item" and item_event["total_cents"] is None
        source.shipping = 0
        await engine.check_watch(wid)
        events = db.list_events()
        assert len(events) == 2
        assert {event["price_scope"] for event in events} == {"item", "total"}
        assert [e["total_cents"] for e in events if e["price_scope"] == "total"] == [2900]
        assert len({e["alert_cents"] for e in events}) == 1
        source.shipping = None
        await engine.check_watch(wid)
        assert len(db.list_events()) == 2, "returning to unknown shipping is not a new event"
        offer_key = Shop
        key = db.list_offers()[0]["key"]
        _, article_alert, _ = db.prior(wid, key, alert_key=item_alert_key(key))
        _, delivered_alert, _ = db.prior(wid, key)
        assert article_alert == delivered_alert == 2900
    finally:
        await engine.close()
        db.close()


@pytest.mark.asyncio
async def test_switching_shipping_state_never_claims_false_price_drop(tmp_path):
    db = Database(tmp_path / "switch.sqlite")
    source = Shop(price=2900, shipping=500)
    engine = Engine(db, {"shop": source})
    wid = db.create_watch(watch())["id"]
    try:
        await engine.check_watch(wid)
        source.shipping = None
        await engine.check_watch(wid)
        assert db.list_events() == [], "29 EUR article price is not a fall from 34 EUR delivered"
        source.shipping = 500
        await engine.check_watch(wid)
        assert db.list_events() == []
        source.shipping = 400
        await engine.check_watch(wid)
        [event] = db.list_events()
        assert event["price_scope"] == "total"
        assert event["comparison_cents"] == 3400
        assert event["alert_cents"] == 3300
    finally:
        await engine.close()
        db.close()


@pytest.mark.asyncio
async def test_opt_in_is_required_and_references_or_unavailable_items_never_alert(tmp_path):
    db = Database(tmp_path / "no-false-alert.sqlite")
    source = Shop(price=1500)
    engine = Engine(db, {"shop": source})
    inactive = db.create_watch(watch(target_cents=2000, alert_on_item_price=False))["id"]
    reference = db.create_watch(watch(target_cents=2000))["id"]
    unavailable = db.create_watch(watch(target_cents=2000))["id"]
    try:
        await engine.check_watch(inactive)
        assert db.list_events() == []
        source.offer_type = "reference"
        await engine.check_watch(reference)
        assert db.list_events() == []
        source.offer_type = "retail"
        source.availability = "out_of_stock"
        await engine.check_watch(unavailable)
        assert db.list_events() == []
    finally:
        await engine.close()
        db.close()


def test_total_price_ceiling_remains_strict_even_with_item_alerts():
    item = Offer(source="shop", merchant="Synthetic shop", product_id="demo",
                 name="Example controller", url="https://example.com/demo",
                 price_cents=2900, shipping_cents=None, checked_at=time.time(), availability="in_stock")
    assert _matches(watch(), item)
    assert not _matches(watch(max_price_cents=5000), item)
    assert not _matches(watch(alert_on_item_price=False, max_price_cents=5000), item)


def test_item_alert_option_is_strict_and_persists_in_local_api(tmp_path):
    with pytest.raises(ValidationError):
        watch(alert_on_item_price="true")
    config = Config(data_dir=tmp_path)
    with TestClient(create_app(config, adapters={"shop": Shop()}, start_scheduler=False),
                    base_url="http://localhost") as client:
        response = client.post("/api/watches", json=watch(target_cents=3000).model_dump())
        assert response.status_code == 201
        assert response.json()["alert_on_item_price"] is True
        assert client.post("/api/watches", json={
            **watch().model_dump(), "alert_on_item_price": "true",
        }).status_code == 422
    with TestClient(create_app(config, adapters={"shop": Shop()}, start_scheduler=False),
                    base_url="http://localhost") as client:
        [saved] = client.get("/api/state").json()["watches"]
        assert saved["alert_on_item_price"] is True
        assert saved["target_cents"] == 3000


@pytest.mark.asyncio
async def test_discord_notification_calls_it_article_price_not_delivered_total():
    class Sink:
        payload = None
        async def post_json(self, url, payload, **kwargs):
            self.payload = payload

    sink = Sink()
    notifier = DiscordNotifier(sink, "https://discord.com/api/webhooks/123/synthetic")
    await notifier.send({
        "product": "Example controller", "reason": "Artikelpreis: Wunschpreis erreicht",
        "merchant": "Test shop", "total_cents": None, "price_cents": 2900,
        "alert_cents": 2900, "price_scope": "item", "comparison_cents": 3500,
        "savings_cents": 600, "checked_at": time.time(), "availability": "in_stock",
        "url": "https://example.com/demo",
    })
    text = sink.payload["content"]
    assert "29,00 € Artikelpreis" in text
    assert "Versandkosten unbekannt" in text
    assert "Gesamtpreis offen" in text
    assert "Bisher beobachteter Artikelpreis" in text
    assert "35,00 €" in text
