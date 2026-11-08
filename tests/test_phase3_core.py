"""Synthetic local API/identity fixtures; not live merchant evidence."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from dealchecker.app import create_app
from dealchecker.config import Config
from dealchecker.db import Database
from dealchecker.models import Offer, WatchInput
from dealchecker.scheduling import interval_pair
from dealchecker.service import _matches


def offer(**kw):
    return Offer(**dict(dict(source='shop', merchant='Synthetic shop', seller='Seller A',
        product_id='123', name='8BitDo Ultimate 2C Blue', model='Ultimate 2C',
        gtin='4006381333931', url='https://example.com/product/123', price_cents=2990,
        shipping_cents=495, availability='in_stock', checked_at=time.time()), **kw))


def watch(**kw):
    return WatchInput(**dict(dict(name='Synthetic shopping', kind='discovery', query='8BitDo Ultimate 2C',
                                 source_ids=['shop']), **kw))


class Shop:
    id, name, description, min_interval_seconds = 'shop', 'Synthetic shop', 'TEST ONLY', 1800
    url_hosts = ('example.com',)
    supports_search = True
    calls = 0

    async def lookup(self, watch):
        self.calls += 1
        return [offer()]

    search = lookup


def test_link_preview_identifies_source_without_creating_watch_then_monitors(tmp_path):
    source = Shop()
    with TestClient(create_app(Config(data_dir=tmp_path), adapters={'shop':source}, start_scheduler=False),
                    base_url='http://localhost') as c:
        r = c.post('/api/links/resolve', json={'url':'https://example.com/product/123'})
        assert r.status_code == 200, r.text
        preview = r.json()
        assert preview['source_id'] == 'shop' and preview['offers'][0]['total_cents'] == 3485
        assert c.get('/api/state').json()['watches'] == []
        selected = preview['offers'][0]
        created = c.post('/api/watches', json=dict(name=selected['name'], kind='exact',
            source_ids=['shop'], product_id=selected['product_id'], url=selected['url'],
            gtin=selected['gtin'], target_cents=3500)).json()
        assert c.post(f"/api/watches/{created['id']}/check",json={}).json()['status'] == 'ok'
        state = c.get('/api/state').json()
        assert len(state['events']) == 1
        assert state['sources'][0]['url_hosts'] == ['example.com']


@pytest.mark.parametrize('url', ['https://amazon.de/dp/B012345678', 'https://unconfigured.example/a',
                               'http://example.com/a','https://user:secret@example.com/a',
                               'https://127.0.0.1/a','https://example.com.evil.example/a'])
def test_unavailable_or_unsafe_link_never_fetches(tmp_path,url):
    source=Shop()
    with TestClient(create_app(Config(data_dir=tmp_path),adapters={'shop':source},start_scheduler=False),
                    base_url='http://localhost') as c:
        assert c.post('/api/links/resolve',json={'url':url}).status_code == 422
        assert source.calls == 0


def test_reference_budget_and_seller_filters_remain_separate():
    ref=offer(offer_type='reference',shipping_cents=None,availability='unknown')
    assert not _matches(watch(max_price_cents=4000), ref)
    assert _matches(watch(max_price_cents=4000,reference_max_price_cents=3000), ref)
    assert not _matches(watch(reference_max_price_cents=2500), ref)
    assert _matches(watch(reference_max_price_cents=100), offer())
    assert _matches(watch(sellers_include=['Seller A']),offer())
    assert not _matches(watch(sellers_exclude=['Seller A']),offer())
    assert not _matches(watch(sellers_include=['Seller A']),offer(seller=''))


def test_discovery_words_and_canonical_gtin_are_verified():
    assert _matches(watch(query='2C 8BitDo Ultimate'),offer())
    assert _matches(watch(kind='exact',query='',product_id='123',gtin='04006381333931'),offer())
    assert _matches(watch(query='4006381333931'),offer())
    assert not _matches(watch(query='Ultimate 2'),offer())  # whole model token, not 2C
    assert not _matches(watch(query='8BitDo',gtin='5901234123457'),offer())


def test_discovery_explicit_model_and_variant_reject_accessories_and_other_variants():
    assert _matches(watch(model='Ultimate 2C', variant='Blue'), offer(variant='Blue'))
    assert not _matches(watch(model='Ultimate 2C'), offer(model='Ultimate 2C case'))
    assert not _matches(watch(variant='Blue'), offer(variant='Pink'))
    with pytest.raises(ValueError, match='requested model'):
        _matches(watch(model='Ultimate 2C'), offer(model=''))


async def test_authorized_feed_preselection_accepts_canonical_gtin_and_reordered_words():
    from dealchecker.adapters.feed import FeedAdapter
    row=offer().model_dump(exclude={'source','checked_at','quality','provenance','key','total_cents'})
    class Catalog:
        async def get_json(self,*args,**kwargs): return {'offers':[row]}
    adapter=FeedAdapter(Catalog(),{'id':'shop','name':'Synthetic shop','url':'https://example.com/catalog'})
    assert len(await adapter.lookup(watch(kind='exact',gtin='04006381333931'))) == 1
    assert len(await adapter.search(watch(query='2C 8BitDo Ultimate'))) == 1
    assert len(await adapter.search(watch(query='4006381333931'))) == 1


def test_migration_preserves_phase2_cadence_and_new_fields(tmp_path):
    path=tmp_path/'db.sqlite'
    db=Database(path)
    row=db.create_watch(watch(kind='exact'))
    data={k:v for k,v in row.items() if k in WatchInput.model_fields}
    for field in ['reference_max_price_cents','sellers_include','sellers_exclude']:
        data.pop(field,None)
    with db.conn:
        db.conn.execute('UPDATE watches SET data=? WHERE id=?',(json.dumps(data),row['id']))
        db.conn.execute('PRAGMA user_version=6')
    db.close()
    db=Database(path)
    migrated=db.get_watch(row['id'])
    assert interval_pair(migrated)==(7200,1800)
    assert migrated['reference_max_price_cents'] is None and migrated['sellers_include']==[]
    fresh=db.create_watch(watch(kind='exact'))
    assert interval_pair(fresh)==(25200,2700)
    db.close()
