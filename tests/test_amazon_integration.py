"""Synthetic configuration/scheduling checks; no live merchant requests."""
from fastapi.testclient import TestClient

from dealchecker.adapters import build_registry
from dealchecker.adapters.amazon import AmazonAdapter
from dealchecker.app import create_app
from dealchecker.config import Config
from dealchecker.models import WatchInput
from dealchecker.reef_settings import ReefSettings
from dealchecker.scheduling import interval_pair


def test_amazon_direct_is_available_without_provider_and_not_replaced_by_key(tmp_path):
    settings = ReefSettings(tmp_path)
    client = object()  # Network calls are not permitted in this configuration test.
    without_provider = build_registry(client)
    assert isinstance(without_provider['amazon'], AmazonAdapter)
    with_provider = build_registry(client, reef_settings=settings)
    assert isinstance(with_provider['amazon'], AmazonAdapter)
    settings.configure('synthetic-key-for-testing-only-12345', 20, '')
    with_key = build_registry(client, reef_settings=settings)
    assert isinstance(with_key['amazon'], AmazonAdapter)


def test_account_free_amazon_watch_can_be_saved_without_network(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path), start_scheduler=False),
                    base_url='http://localhost') as client:
        response = client.post('/api/watches', json={
            'name': 'Synthetic ASIN', 'source_ids': ['amazon'], 'product_id': 'B012345678',
        })
        assert response.status_code == 201
        sources = {s['id']: s for s in client.get('/api/state').json()['sources']}
        assert sources['amazon']['configured']
        assert sources['amazon']['supports_search']
        assert not sources['ebay']['configured']


def test_amazon_cadence_respects_priority_overrides_and_source_floor():
    watch = WatchInput(name='Synthetic ASIN', product_id='B012345678', source_ids=['amazon']).model_dump()
    assert interval_pair(watch, 'amazon', 1800) == (7200, 1800)
    assert interval_pair(watch | {'kind': 'discovery'}, 'amazon', 1800) == (28800, 10800)
    assert interval_pair(watch | {'priority': 'high'}, 'amazon', 1800) == (2700, 1800)
    assert interval_pair(watch | {'interval_seconds': 10800, 'black_friday_interval_seconds': 3600},
                         'amazon', 1800) == (10800, 3600)
    assert interval_pair(watch | {'source_intervals': {'amazon': {
        'normal_seconds': 900, 'black_friday_seconds': 300}}}, 'amazon', 1800) == (1800, 1800)
