"""Full local API tests; fixture source is synthetic and never in live registry."""
import time

import pytest
from fastapi.testclient import TestClient

from dealchecker.app import create_app
from dealchecker.config import Config
from dealchecker.models import Offer


class FixtureSource:
    id, name, description, min_interval_seconds = 'fixture', 'Synthetic test source', 'TEST ONLY', 300
    price = 1000
    fail = False

    async def search(self, watch):
        if self.fail: raise ValueError('Synthetic source failure')
        return [Offer(source='fixture', merchant='Test merchant', product_id='one', name='Test Widget',
                      url='https://example.com/one', price_cents=self.price, shipping_cents=200,
                      availability='in_stock', checked_at=time.time(), provenance='test')]
    lookup = search


@pytest.fixture
def client(tmp_path):
    source = FixtureSource()
    config = Config(data_dir=tmp_path)
    with TestClient(create_app(config, adapters={'fixture':source}, start_scheduler=False),
                    base_url='http://127.0.0.1:8765') as c:
        c.source = source
        yield c


def watch_payload(**kw):
    return dict(name='My widget', query='Test Widget', kind='discovery', source_ids=['fixture'], target_cents=1500, **kw)


def test_vertical_workflow_changes_history_alarms_and_edit(client):
    response = client.post('/api/watches', json=watch_payload())
    assert response.status_code == 201, response.text
    watch_id = response.json()['id']
    assert client.post(f'/api/watches/{watch_id}/check', json={}).json()['status'] == 'ok'
    state = client.get('/api/state').json()
    assert state['offers'][0]['total_cents'] == 1200
    assert len(state['events']) == 1
    assert state['notification_configured'] is False
    offer_id = state['offers'][0]['id']
    client.post(f'/api/watches/{watch_id}/check', json={})
    assert len(client.get('/api/state').json()['events']) == 1
    client.source.price = 800
    client.post(f'/api/watches/{watch_id}/check', json={})
    assert len(client.get('/api/state').json()['events']) == 2
    history = client.get(f'/api/offers/{offer_id}/history').json()
    assert [row['total_cents'] for row in history] == [1200, 1200, 1000]
    edited = watch_payload(paused=True, priority='critical')
    assert client.put(f'/api/watches/{watch_id}', json=edited).status_code == 200
    assert client.post(f'/api/watches/{watch_id}/check', json={}).json()['status'] == 'paused'
    assert client.delete(f'/api/watches/{watch_id}').status_code == 200
    assert client.get('/api/state').json()['watches'] == []


def test_source_failure_visible_last_known_offer_retained(client):
    id = client.post('/api/watches', json=watch_payload()).json()['id']
    client.post(f'/api/watches/{id}/check', json={})
    client.source.fail = True
    assert client.post(f'/api/watches/{id}/check', json={}).json()['status'] == 'error'
    state = client.get('/api/state').json()
    assert state['watches'][0]['last_error']
    assert state['offers'][0]['stale']
    assert state['checks']


@pytest.mark.parametrize('headers', [
    {'Host':'evil.example'}, {'Origin':'https://evil.example'}, {'Origin':'null'},
    {'Origin':'http://localhost:9999'}, {'Sec-Fetch-Site':'cross-site'},
])
def test_hostile_browser_cannot_mutate_local_data(client, headers):
    assert client.post('/api/watches', json=watch_payload(), headers=headers).status_code == 403
    assert client.get('/api/state').json()['watches'] == []


def test_json_body_size_and_validation(client):
    assert client.post('/api/watches', content='x', headers={'Content-Type':'text/plain'}).status_code == 415
    assert client.post('/api/watches', json={'name':'x'*70000}).status_code == 413
    assert client.post('/api/watches', json=watch_payload(interval_seconds=2)).status_code == 422
    invalid = watch_payload()
    invalid['source_ids'] = ['not-a-source']
    assert client.post('/api/watches', json=invalid).status_code == 422
    assert client.delete('/api/watches/999').status_code == 404


def test_settings_validation_and_security_headers(client):
    assert client.put('/api/settings', json={'notification_enabled':True}).status_code == 422
    assert client.put('/api/settings', json={'black_friday_until':time.time()+90000}).status_code == 200
    assert client.put('/api/settings', json={'black_friday_until':time.time()+7*86400+3600}).status_code == 422
    assert client.put('/api/settings', json={'paused':'yes'}).status_code == 422
    assert client.put('/api/settings', json={'paused':True}).json()['paused'] is True
    response = client.get('/api/health')
    assert response.json()['status'] == 'ok'
    assert "default-src 'self'" in response.headers['Content-Security-Policy']


def test_state_exposes_effective_source_schedule_and_safe_comparisons(client):
    payload = watch_payload(black_friday_interval_seconds=1200,
                            source_intervals={'fixture': {'normal_seconds': 5400, 'black_friday_seconds': 600}})
    created = client.post('/api/watches', json=payload)
    assert created.status_code == 201
    state = client.get('/api/state').json()
    source = state['watches'][0]['effective_source_intervals']['fixture']
    assert source['normal_seconds'] == 5400
    assert source['black_friday_seconds'] == 600
    assert source['effective_seconds'] == 5400
    assert source['min_interval_seconds'] == 300
    assert source['next_due'] == state['watches'][0]['next_due']
    assert state['comparisons'] == []


def test_sources_report_their_own_success_and_failure(tmp_path):
    good, bad = FixtureSource(), FixtureSource()
    bad.id, bad.name, bad.fail = 'bad', 'Broken fixture', True
    with TestClient(create_app(Config(data_dir=tmp_path), adapters={'fixture':good,'bad':bad}, start_scheduler=False),
                    base_url='http://localhost') as c:
        payload = watch_payload()
        payload['source_ids'] = ['fixture','bad']
        id = c.post('/api/watches', json=payload).json()['id']
        assert c.post(f'/api/watches/{id}/check', json={}).json()['status'] == 'error'
        state = c.get('/api/state').json()
        sources = {s['id']:s for s in state['sources']}
        assert sources['fixture']['last_error'] is None
        assert sources['fixture']['last_success'] > 0
        assert sources['bad']['last_error']
        assert sources['bad']['last_success'] is None
        assert state['offers'][0]['stale'] is False


def test_data_and_next_due_survive_application_restart(tmp_path):
    config = Config(data_dir=tmp_path)
    source = FixtureSource()
    def app(): return create_app(config, adapters={'fixture':source}, start_scheduler=False)
    with TestClient(app(), base_url='http://localhost') as c:
        id = c.post('/api/watches', json=watch_payload()).json()['id']
        c.post(f'/api/watches/{id}/check', json={})
        due = c.get('/api/state').json()['watches'][0]['next_due']
    with TestClient(app(), base_url='http://localhost') as c:
        state = c.get('/api/state').json()
        assert len(state['offers']) == 1
        assert len(state['events']) == 1
        assert state['watches'][0]['next_due'] == due
        c.post(f'/api/watches/{id}/check', json={})
        assert len(c.get('/api/state').json()['events']) == 1


def test_api_healthy_source_keeps_schedule_and_alerts_after_long_restart(tmp_path, monkeypatch):
    """Exercise actual API/persistence with synthetic sources and a four-hour outage."""
    clock = [time.time()]
    monkeypatch.setattr('dealchecker.service.time.time', lambda: clock[0])
    good, bad = FixtureSource(), FixtureSource()
    bad.id, bad.name, bad.fail = 'bad', 'Broken fixture', True
    config = Config(data_dir=tmp_path)
    def app():
        return create_app(config, adapters={'fixture':good, 'bad':bad}, start_scheduler=False)
    payload = watch_payload(priority='critical', interval_seconds=300, black_friday_interval_seconds=300)
    payload['source_ids'] = ['fixture', 'bad']
    with TestClient(app(), base_url='http://localhost') as c:
        id = c.post('/api/watches', json=payload).json()['id']
        assert c.post(f'/api/watches/{id}/check', json={}).json()['status'] == 'error'
        state = c.get('/api/state').json()
        assert state['watches'][0]['next_due'] == pytest.approx(clock[0] + 300, rel=0, abs=.01)
        assert [e['total_cents'] for e in state['events']] == [1200]
    clock[0] += 4 * 3600
    good.price = 800
    with TestClient(app(), base_url='http://localhost') as c:
        c.post(f'/api/watches/{id}/check', json={})
        state = c.get('/api/state').json()
        assert sorted(e['total_cents'] for e in state['events']) == [1000, 1200]
        assert state['watches'][0]['next_due'] == pytest.approx(clock[0] + 300, rel=0, abs=.01)
        assert state['offers'][0]['stale'] is False


def test_physical_discovery_to_exact_monitoring_after_restart(tmp_path, monkeypatch):
    """Real Modula24 adapter and local API; merchant replies below are synthetic."""
    from dealchecker.adapters.modula24 import Modula24Adapter

    clock = [time.time()]
    monkeypatch.setattr('dealchecker.service.time.time', lambda: clock[0])
    monkeypatch.setattr('dealchecker.adapters.modula24.time', lambda: clock[0])

    class Catalog:
        price = '10.00 EUR'
        calls = 0

        async def get_json(self, url, **kwargs):
            self.calls += 1
            assert kwargs['allowed_hosts'] == {'modula24.com'}
            if url == 'https://modula24.com/products/synthetic-konnektor.json':
                return {'product': {
                    'handle': 'synthetic-konnektor', 'title': 'Synthetic Konnektor',
                    'vendor': 'Modula24', 'product_type': 'Konnektoren',
                    'options': [{'name': 'Title', 'position': 1}],
                    'variants': [{'id': 100, 'title': 'Default Title', 'price': self.price.split()[0],
                                  'available': True, 'option1': 'Default Title'}],
                }}
            assert url == 'https://modula24.com/feeds/products.de.json'
            return {'generated': '2026-01-01T00:00:00Z', 'language': 'de', 'currency': 'EUR', 'items': [{
                'id': 'M24-100', 'variant_id': '100', 'title': 'Synthetic Konnektor',
                'link': 'https://modula24.com/products/synthetic-konnektor',
                'price': self.price, 'availability': 'in_stock', 'brand': 'Modula24',
                'product_type': 'Konnektoren', 'options': {},
                'shipping': [{'countries': ['DE'], 'price': '3.99 EUR'}],
            }]}

        async def post_json_response(self, url, payload, **kwargs):
            self.calls += 1
            assert url == 'https://modula24.com/api/checkout/shipping'
            assert payload == {'country': 'DE', 'items': [{'id': '100', 'qty': 1}], 'currency': 'EUR'}
            return {'country': 'DE', 'currency': 'EUR', 'shipping_options': [{'id': 'dhl', 'cost': 3.99}]}

    catalog = Catalog()
    config = Config(data_dir=tmp_path)

    def app():
        return create_app(config, adapters={'modula24': Modula24Adapter(catalog)}, start_scheduler=False)

    with TestClient(app(), base_url='http://localhost') as c:
        discovery = c.post('/api/watches', json={
            'name': 'Synthetic furniture search', 'kind': 'discovery',
            'query': 'Konnektor', 'source_ids': ['modula24'],
        }).json()['id']
        assert c.post(f'/api/watches/{discovery}/check', json={}).json()['status'] == 'ok'
        found = c.get('/api/state').json()['offers'][0]
        assert found['product_id'] == '100'
        assert found['total_cents'] == 1399
        exact = c.post('/api/watches', json={
            'name': 'Synthetic selected variant', 'kind': 'exact',
            'product_id': found['product_id'], 'url': found['url'],
            'source_ids': ['modula24'], 'target_cents': 1500,
        }).json()['id']
        assert c.post(f'/api/watches/{exact}/check', json={}).json()['status'] == 'ok'
        assert catalog.calls == 3  # discovery feed, targeted product, shipping quote
        state = c.get('/api/state').json()
        assert [e['total_cents'] for e in state['events']] == [1399]
        exact_offer = next(o for o in state['offers'] if o['watch_id'] == exact)

    clock[0] += 4 * 3600
    catalog.price = '8.00 EUR'
    with TestClient(app(), base_url='http://localhost') as c:
        assert c.post(f'/api/watches/{exact}/check', json={}).json()['status'] == 'ok'
        state = c.get('/api/state').json()
        assert [e['total_cents'] for e in state['events']] == [1199, 1399]
        assert catalog.calls == 5  # restart exact lookup needs no full catalogue
        history = c.get(f"/api/offers/{exact_offer['id']}/history").json()
        assert [h['total_cents'] for h in history] == [1399, 1199]
