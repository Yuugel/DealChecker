"""Synthetic webhook configuration; these tests never send external messages."""
from fastapi.testclient import TestClient
import pytest

from dealchecker.app import create_app
from dealchecker.config import Config


WEBHOOK='https://discord.com/api/webhooks/123456789/NOT_A_REAL_SECRET'


def test_webhook_configuration_is_redacted_persistent_and_separate_from_enable(tmp_path):
    config=Config(data_dir=tmp_path)
    def app(): return create_app(config,adapters={},start_scheduler=False)
    with TestClient(app(),base_url='http://localhost') as c:
        assert c.get('/api/state').json()['notification_configuration_source']=='none'
        r=c.put('/api/notifications/config',json={'webhook_url':WEBHOOK})
        assert r.status_code==200 and 'NOT_A_REAL_SECRET' not in r.text
        state=c.get('/api/state')
        assert 'NOT_A_REAL_SECRET' not in state.text
        assert state.json()['notification_configured'] is True
        assert state.json()['notification_configuration_source']=='local'
        assert state.json()['settings']['notification_enabled'] is False
    with TestClient(app(),base_url='http://localhost') as c:
        assert c.get('/api/state').json()['notification_configured'] is True
        assert c.put('/api/settings',json={'notification_enabled':True}).status_code==200
        assert c.delete('/api/notifications/config').status_code==200
        state=c.get('/api/state').json()
        assert not state['notification_configured'] and not state['settings']['notification_enabled']
        assert not (tmp_path/'notifications.json').exists()


def test_invalid_configuration_never_echoes_secret_or_overwrites_good_config(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path),adapters={},start_scheduler=False),base_url='http://localhost') as c:
        assert c.put('/api/notifications/config',json={'webhook_url':WEBHOOK}).status_code==200
        for payload in [{'webhook_url':'https://evil.example/NOT_A_REAL_SECRET'},
                        {'webhook_url':'https://discord.com/api/webhooks/../NOT_A_REAL_SECRET'},
                        {'webhook_url':'https://discord.com/api/webhooks/%2e%2e/NOT_A_REAL_SECRET'},
                        {'webhook_url':'https://discord.com/api/webhooks/123/NOT_A_REAL_SECRET\npath'},
                        {'webhook_url':WEBHOOK,'unexpected':'NOT_A_REAL_SECRET'},
                        {'webhook_url':['NOT_A_REAL_SECRET']}]:
            r=c.put('/api/notifications/config',json=payload)
            assert r.status_code==422 and 'NOT_A_REAL_SECRET' not in r.text
        assert c.get('/api/state').json()['notification_configured']
        r=c.put('/api/notifications/config',json={'webhook_url':WEBHOOK},headers={'Origin':'https://evil.example'})
        assert r.status_code==403


def test_environment_config_remains_explicit_and_cannot_be_overwritten(tmp_path):
    config=Config(data_dir=tmp_path,discord_webhook=WEBHOOK)
    with TestClient(create_app(config,adapters={},start_scheduler=False),base_url='http://localhost') as c:
        assert c.get('/api/state').json()['notification_configuration_source']=='environment'
        assert c.put('/api/notifications/config',json={'webhook_url':WEBHOOK}).status_code==409
        assert c.delete('/api/notifications/config').status_code==409


def test_invalid_saved_secret_does_not_stop_monitor_and_can_be_repaired(tmp_path):
    (tmp_path/'notifications.json').write_text('{"bad":"NOT_A_REAL_SECRET"}',encoding='utf-8')
    with TestClient(create_app(Config(data_dir=tmp_path),adapters={},start_scheduler=False),base_url='http://localhost') as c:
        state=c.get('/api/state')
        assert state.status_code==200 and 'NOT_A_REAL_SECRET' not in state.text
        assert state.json()['notification_configuration_error']
        assert not state.json()['notification_configured']
        assert c.put('/api/notifications/config',json={'webhook_url':WEBHOOK}).status_code==200
        assert c.get('/api/state').json()['notification_configuration_error'] is None


async def test_waiting_delivery_rechecks_configuration_after_removal(tmp_path):
    import asyncio
    from dealchecker.db import Database
    from dealchecker.service import Engine
    db=Database(tmp_path/'delivery.sqlite')
    db.update_settings({'notification_enabled':True})
    engine=Engine(db, {}, notifier=object())
    calls=[]
    db.due_outbox=lambda **kw: calls.append('read') or []
    async with engine._delivery_lock:
        task=asyncio.create_task(engine.flush_outbox())
        await asyncio.sleep(0)
        engine.notifier=None
    await task
    assert calls==[]
    db.close()


def seed_pending(db):
    import time
    from dealchecker.models import WatchInput, Offer
    watch=db.create_watch(WatchInput(name='Synthetic notification',query='Example',source_ids=['test']))
    offer=Offer(source='test',merchant='Synthetic',product_id='1',name='Example',
                url='https://example.com/1',price_cents=100,shipping_cents=0,
                availability='in_stock',checked_at=time.time())
    db.update_settings({'notification_enabled':True})
    db.record_check(watch['id'],[(offer,'Wunschpreis erreicht',None,None,'Wunschpreis erreicht')],
                    'ok',None,time.time())
    assert len(db.due_outbox())==1


@pytest.mark.parametrize('remove_first',[False,True])
def test_new_destination_cannot_receive_previous_destination_queue(tmp_path,remove_first):
    app=create_app(Config(data_dir=tmp_path),adapters={},start_scheduler=False)
    with TestClient(app,base_url='http://localhost') as c:
        c.put('/api/notifications/config',json={'webhook_url':WEBHOOK})
        seed_pending(app.state.db)
        if remove_first: assert c.delete('/api/notifications/config').status_code==200
        c.put('/api/notifications/config',json={'webhook_url':WEBHOOK.replace('NOT_A_REAL_SECRET','OTHER_SYNTHETIC')})
        c.put('/api/settings',json={'notification_enabled':True})
        assert app.state.db.due_outbox()==[]
        assert c.get('/api/state').json()['events'][0]['notification_status']=='cancelled'


def test_restart_preserves_same_destination_queue_but_cancels_environment_rotation(tmp_path):
    def make(url): return create_app(Config(data_dir=tmp_path,discord_webhook=url),adapters={},start_scheduler=False)
    first=make(WEBHOOK)
    with TestClient(first,base_url='http://localhost'):
        seed_pending(first.state.db)
    same=make(WEBHOOK)
    with TestClient(same,base_url='http://localhost'):
        assert len(same.state.db.due_outbox())==1
    changed=make(WEBHOOK.replace('NOT_A_REAL_SECRET','OTHER_SYNTHETIC'))
    with TestClient(changed,base_url='http://localhost') as c:
        assert changed.state.db.due_outbox()==[]
        assert 'OTHER_SYNTHETIC' not in c.get('/api/state').text
