import pytest

from dealchecker.config import Config, InstanceLock


def test_single_instance_lock_released_after_exit(tmp_path):
    path = tmp_path / 'database.lock'
    with InstanceLock(path):
        with pytest.raises(RuntimeError):
            with InstanceLock(path): pass
    with InstanceLock(path): pass


def test_config_never_exposes_webhook_in_repr(monkeypatch, tmp_path):
    monkeypatch.setenv('DEALCHECKER_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DEALCHECKER_DISCORD_WEBHOOK', 'https://discord.com/api/webhooks/1/SECRET')
    config = Config.from_env()
    assert config.data_dir == tmp_path
    assert 'SECRET' not in repr(config)


def test_invalid_feed_configuration_is_explicit(monkeypatch):
    monkeypatch.setenv('DEALCHECKER_FEEDS', 'not-json')
    with pytest.raises(ValueError, match='DEALCHECKER_FEEDS'):
        Config.from_env()
