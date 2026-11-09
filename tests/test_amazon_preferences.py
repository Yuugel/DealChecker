"""Synthetic Prime preference regression tests. No merchant requests."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from dealchecker.app import create_app
from dealchecker.config import Config
from dealchecker.db import Database
from dealchecker.models import Offer, WatchInput
from dealchecker.notifications import DiscordNotifier
from dealchecker.service import Engine


def watch(**changes):
    return WatchInput(**({'name': 'Synthetic controller', 'product_id': 'B012345678',
                         'source_ids': ['amazon']} | changes))


class Amazon:
    id = 'amazon'
    name = 'Synthetic Amazon'
    description = 'Synthetic test fixture'
    supports_search = True
    url_hosts = ('www.amazon.de',)
    min_interval_seconds = 1800
    prime_price = 4000
    shipping = 0
    latest_preference = None

    async def lookup(self, value):
        self.latest_preference = value.amazon_prime
        return [Offer(source='amazon', merchant='Amazon', seller='Synthetic Seller', product_id='B012345678',
            name='Synthetic controller', url='https://www.amazon.de/dp/B012345678',
            price_cents=self.prime_price if value.amazon_prime else 5000,
            price_basis='prime' if value.amazon_prime else 'regular', shipping_cents=self.shipping,
            availability='in_stock', checked_at=time.time())]


@pytest.mark.asyncio
async def test_price_basis_change_does_not_create_false_discount_and_histories_stay_separate(tmp_path):
    db = Database(tmp_path / 'prices.db')
    adapter = Amazon()
    engine = Engine(db, {'amazon': adapter})
    row = db.create_watch(watch())
    try:
        assert (await engine.check_watch(row['id']))['status'] == 'ok'
        db.update_watch(row['id'], watch(amazon_prime=True))
        assert (await engine.check_watch(row['id']))['status'] == 'ok'
        assert db.list_events() == []  # Regular €50 -> Prime €40 is not a price drop.
        adapter.prime_price = 3500
        assert (await engine.check_watch(row['id']))['status'] == 'ok'
        [event] = db.list_events()
        assert event['price_basis'] == 'prime'
        assert event['comparison_cents'] == 4000
        assert event['total_cents'] == 3500
        offers = {o['price_basis']: o for o in db.list_offers()}
        assert len(db.history(offers['regular']['id'])) == 1
        assert len(db.history(offers['prime']['id'])) == 2
        db.update_watch(row['id'], watch(amazon_prime=False))
        assert next(o for o in db.list_offers() if o['price_basis'] == 'prime')['stale']
    finally:
        await engine.close()
        db.close()


@pytest.mark.asyncio
async def test_prime_does_not_imply_free_shipping_or_alarm(tmp_path):
    db = Database(tmp_path / 'unknown.db')
    adapter = Amazon()
    adapter.shipping = None
    engine = Engine(db, {'amazon': adapter})
    try:
        row = db.create_watch(watch(amazon_prime=True, target_cents=10000))
        assert (await engine.check_watch(row['id']))['status'] == 'ok'
        assert db.list_events() == []
        assert db.list_offers()[0]['total_cents'] is None
    finally:
        await engine.close()
        db.close()


@pytest.mark.asyncio
async def test_existing_watch_without_prime_field_still_checks_and_updates(tmp_path):
    path = tmp_path / 'legacy.db'
    db = Database(path)
    row = db.create_watch(watch())
    data = watch().model_dump()
    del data['amazon_prime']
    with db.conn:
        db.conn.execute('UPDATE watches SET data=? WHERE id=?', (json.dumps(data), row['id']))
    db.close()
    db = Database(path)
    engine = Engine(db, {'amazon': Amazon()})
    try:
        assert db.get_watch(row['id'])['amazon_prime'] is False
        assert (await engine.check_watch(row['id']))['status'] == 'ok'
        assert db.update_watch(row['id'], watch(amazon_prime=True))['amazon_prime']
    finally:
        await engine.close()
        db.close()


def test_link_preview_prime_selection_and_saved_preference_survive_restart(tmp_path):
    adapter = Amazon()
    config = Config(data_dir=tmp_path)
    with TestClient(create_app(config, adapters={'amazon': adapter}, start_scheduler=False),
                    base_url='http://localhost') as client:
        preview = client.post('/api/links/resolve', json={
            'url': 'https://www.amazon.de/dp/B012345678', 'amazon_prime': True})
        assert preview.status_code == 200
        assert adapter.latest_preference is True
        assert preview.json()['offers'][0]['price_basis'] == 'prime'
        assert preview.json()['amazon_prime'] is True
        response = client.post('/api/watches', json=watch(amazon_prime=True).model_dump())
        assert response.status_code == 201
        assert client.post('/api/links/resolve', json={
            'url': 'https://www.amazon.de/dp/B012345678', 'amazon_prime': 'false'}).status_code == 422
    with TestClient(create_app(config, adapters={'amazon': adapter}, start_scheduler=False),
                    base_url='http://localhost') as client:
        assert client.get('/api/state').json()['watches'][0]['amazon_prime'] is True


@pytest.mark.asyncio
async def test_prime_notification_labels_membership_condition():
    class Transport:
        payload = None

        async def post_json(self, url, payload, **kwargs):
            self.payload = payload

    transport = Transport()
    notifier = DiscordNotifier(transport, 'https://discord.com/api/webhooks/123/synthetic')
    await notifier.send({'reason': 'Wunschpreis erreicht', 'product': 'Synthetic controller',
        'total_cents': 4000, 'merchant': 'Amazon', 'checked_at': time.time(),
        'availability': 'in_stock', 'url': 'https://www.amazon.de/dp/B012345678', 'price_basis': 'prime'})
    assert 'Prime-Preis' in transport.payload['content']
    assert 'Mitgliedschaft erforderlich' in transport.payload['content']
