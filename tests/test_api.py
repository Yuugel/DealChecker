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
    assert client.put('/api/settings', json={'black_friday_until':time.time()+90000}).status_code == 422
    assert client.put('/api/settings', json={'paused':'yes'}).status_code == 422
    assert client.put('/api/settings', json={'paused':True}).json()['paused'] is True
    response = client.get('/api/health')
    assert response.json()['status'] == 'ok'
    assert "default-src 'self'" in response.headers['Content-Security-Policy']


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
