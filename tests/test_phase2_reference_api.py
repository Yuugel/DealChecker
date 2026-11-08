"""Synthetic responses through the real reference adapter and persistent local API."""
import time

from fastapi.testclient import TestClient

from dealchecker.adapters.bestenvergleich import BestenvergleichAdapter
from dealchecker.app import create_app
from dealchecker.config import Config


def test_reference_discovery_exact_restart_price_change_never_becomes_purchase_alarm(tmp_path, monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr('dealchecker.adapters.bestenvergleich.time', lambda: clock[0])
    monkeypatch.setattr('dealchecker.service.time.time', lambda: clock[0])

    class Catalog:
        price = 49.99
        calls = []

        async def get_json(self, url, **kwargs):
            self.calls.append(url)
            assert kwargs['allowed_hosts'] == {'bestenvergleich.de'}
            row = dict(id=1234, name='Synthetic Airfryer', ean='4006381333931',
                       url='https://bestenvergleich.de/fritteusen/synthetic-1234',
                       price_min=self.price, offer_count=5)
            if '/search?' in url:
                return {'results': [row]}
            assert url.endswith('/products/1234')
            return row

    catalog = Catalog()
    config = Config(data_dir=tmp_path)

    def app():
        return create_app(config, adapters={'bestenvergleich': BestenvergleichAdapter(catalog)},
                          start_scheduler=False)

    with TestClient(app(), base_url='http://localhost') as c:
        payload = dict(name='Synthetic reference search', kind='discovery', query='Airfryer',
                       source_ids=['bestenvergleich'], target_cents=10000)
        discovery = c.post('/api/watches', json=payload).json()['id']
        assert c.post(f'/api/watches/{discovery}/check', json={}).json()['status'] == 'ok'
        found = c.get('/api/state').json()['offers'][0]
        assert found['offer_type'] == 'reference' and found['total_cents'] is None
        exact = c.post('/api/watches', json=dict(payload, kind='exact', query='',
                       product_id=found['product_id'], url=found['url'], gtin=found['gtin'])).json()['id']
        assert c.post(f'/api/watches/{exact}/check', json={}).json()['status'] == 'ok'
        state = c.get('/api/state').json()
        exact_offer = next(o for o in state['offers'] if o['watch_id'] == exact)
        assert state['events'] == [] and state['comparisons'] == []
        capped = c.post('/api/watches', json=dict(payload, max_price_cents=10000)).json()['id']
        c.post(f'/api/watches/{capped}/check', json={})
        assert not any(o['watch_id'] == capped for o in c.get('/api/state').json()['offers'])
        assert len(catalog.calls) == 2  # identical discovery uses bounded cache

    clock[0] += 4 * 3600
    catalog.price = 29.99
    with TestClient(app(), base_url='http://localhost') as c:
        assert c.post(f'/api/watches/{exact}/check', json={}).json()['status'] == 'ok'
        state = c.get('/api/state').json()
        assert state['events'] == [] and state['comparisons'] == []
        current = next(o for o in state['offers'] if o['watch_id'] == exact)
        assert current['evaluation'] == 'Referenzpreis'
        history = c.get(f"/api/offers/{exact_offer['id']}/history").json()
        assert [h['price_cents'] for h in history] == [4999, 2999]
        assert all(h['total_cents'] is None for h in history)
        assert len(catalog.calls) == 3
