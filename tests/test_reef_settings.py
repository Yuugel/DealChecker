"""Synthetic credentials and budgets: no external calls or paid credits."""
import json

import pytest

from dealchecker.reef_settings import ReefSettings

KEY = 'SYNTHETIC_NOT_A_REAL_REEF_KEY'


def test_budget_persists_across_restart_key_change_and_removal(tmp_path):
    settings = ReefSettings(tmp_path)
    settings.configure(KEY, 5)
    settings.reserve(3)
    restarted = ReefSettings(tmp_path)
    assert restarted.status()['reserved_credits_today'] == 3
    assert KEY not in str(restarted.status())
    restarted.clear()
    with pytest.raises(ValueError, match='Zugang fehlt'):
        restarted.reserve(1)
    restarted.configure(KEY + '_ROTATED', 5)
    restarted.reserve(2)
    with pytest.raises(ValueError, match='Tageslimit'):
        restarted.reserve(1)


def test_budget_day_rollover_and_clock_reversal(tmp_path, monkeypatch):
    monkeypatch.setattr(ReefSettings, '_today', staticmethod(lambda: '2026-11-08'))
    settings = ReefSettings(tmp_path)
    settings.configure(KEY, 2)
    settings.reserve(2)
    monkeypatch.setattr(ReefSettings, '_today', staticmethod(lambda: '2026-11-07'))
    with pytest.raises(ValueError, match='Tageslimit'):
        settings.reserve(1)
    monkeypatch.setattr(ReefSettings, '_today', staticmethod(lambda: '2026-11-09'))
    settings.reserve(1)
    assert settings.status()['reserved_credits_today'] == 1


def test_corrupt_usage_cannot_reset_budget(tmp_path):
    settings = ReefSettings(tmp_path)
    settings.configure(KEY, 5)
    settings.usage_path.write_text('{invalid', encoding='utf-8')
    restarted = ReefSettings(tmp_path)
    assert restarted.status()['error']
    restarted.configure(KEY, 5)
    with pytest.raises(ValueError, match='beschädigt'):
        restarted.reserve(1)


@pytest.mark.parametrize('key,limit', [('short', 20), (KEY + '\r\nX: a', 20), (KEY, True), (KEY, 0), (KEY, 501)])
def test_bad_config_preserves_previous_secret(tmp_path, key, limit):
    settings = ReefSettings(tmp_path)
    settings.configure(KEY, 20)
    with pytest.raises(ValueError):
        settings.configure(key, limit)
    assert ReefSettings(tmp_path).key() == KEY
    assert settings.generation == 1


def test_environment_priority_and_invalid_saved_config(tmp_path):
    (tmp_path / 'reef.json').write_text(json.dumps({'api_key': KEY}), encoding='utf-8')
    assert not ReefSettings(tmp_path).configured
    assert ReefSettings(tmp_path).error
    settings = ReefSettings(tmp_path, KEY)
    assert settings.configured
    with pytest.raises(ValueError, match='Prozessumgebung'):
        settings.configure(KEY, 20)
    with pytest.raises(ValueError, match='Prozessumgebung'):
        settings.clear()


def test_local_api_never_echoes_secret_persists_and_is_origin_guarded(tmp_path):
    from fastapi.testclient import TestClient
    from dealchecker.app import create_app
    from dealchecker.config import Config
    config = Config(data_dir=tmp_path)
    def make(): return create_app(config, adapters={}, start_scheduler=False)
    with TestClient(make(), base_url='http://localhost') as client:
        assert not client.get('/api/state').json()['reef']['configured']
        response = client.put('/api/providers/reef', json={'api_key': KEY, 'daily_credit_limit': 9})
        assert response.status_code == 200 and KEY not in response.text
        assert response.json()['configured']
        assert KEY not in client.get('/api/state').text
        for bad in [{'api_key': [KEY]}, {'api_key': KEY, 'unknown': KEY}, {'api_key': KEY, 'daily_credit_limit': True}]:
            response = client.put('/api/providers/reef', json=bad)
            assert response.status_code == 422 and KEY not in response.text
        assert client.delete('/api/providers/reef', headers={'Origin': 'https://evil.example'}).status_code == 403
    with TestClient(make(), base_url='http://localhost') as client:
        assert client.get('/api/state').json()['reef']['daily_credit_limit'] == 9
        assert client.delete('/api/providers/reef').status_code == 200
        assert not client.get('/api/state').json()['reef']['configured']


def test_environment_credential_cannot_be_removed_via_api(tmp_path):
    from fastapi.testclient import TestClient
    from dealchecker.app import create_app
    from dealchecker.config import Config
    with TestClient(create_app(Config(data_dir=tmp_path, reef_api_key=KEY), adapters={}, start_scheduler=False),
                    base_url='http://localhost') as client:
        assert client.put('/api/providers/reef', json={'api_key': KEY}).status_code == 409
        assert client.delete('/api/providers/reef').status_code == 409
        assert KEY not in client.get('/api/state').text


def test_optional_sources_cannot_be_used_until_key_is_configured(tmp_path):
    from fastapi.testclient import TestClient
    from dealchecker.app import create_app
    from dealchecker.config import Config
    with TestClient(create_app(Config(data_dir=tmp_path), start_scheduler=False), base_url='http://localhost') as client:
        state = client.get('/api/state').json()
        sources = {source['id']: source for source in state['sources']}
        assert sources['mediamarkt']['configured'] and sources['saturn']['configured']
        assert not sources['amazon']['configured']
        assert client.post('/api/links/resolve', json={'url': 'https://www.amazon.de/dp/B012345678'}).status_code == 422
        assert client.post('/api/watches', json={'name': 'Synthetic', 'query': 'test', 'source_ids': ['amazon']}).status_code == 422
        client.put('/api/providers/reef', json={'api_key': KEY, 'delivery_postcode': '10115'})
        assert all(source['configured'] for source in client.get('/api/state').json()['sources'])
        assert client.get('/api/state').json()['reef']['delivery_postcode'] == '10115'
