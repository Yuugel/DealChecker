"""Synthetic HTML -> adapter -> API -> SQLite regression; not live shop evidence."""
import json

from fastapi.testclient import TestClient

from dealchecker.adapters.alternate import AlternateAdapter
from dealchecker.app import create_app
from dealchecker.config import Config


URL='https://www.alternate.de/Example/Radio/html/product/123456'


class Pages:
    def __init__(self, price): self.price=price

    async def get_text(self, url, **kwargs):
        if url.endswith('/robots.txt'): return 'User-agent: *\nDisallow: /private/'
        if '/listing.xhtml?' in url:
            return f'<a class="productBox" href="{URL}">Example Radio</a>'
        assert url==URL
        product={'@type':'Product','sku':'123456','name':'Example Radio',
                 'gtin13':'4006381333931','brand':{'name':'Example'},'mpn':'RADIO-100',
                 'offers':{'@type':'Offer','url':URL,'price':self.price,'priceCurrency':'EUR',
                           'seller':{'name':'ALTERNATE GmbH'},'availability':'https://schema.org/InStock',
                           'shippingDetails':{'shippingDestination':{'addressCountry':'DE'},
                                              'shippingRate':{'currency':'EUR','value':'7.99'}}}}
        return '<script type="application/ld+json">'+json.dumps(product)+'</script>'


class Broken:
    id,name,description,min_interval_seconds='broken','Synthetic failure','TEST ONLY',1800
    async def search(self, watch): raise ValueError('synthetic permanent failure')


def test_html_price_drop_after_restart_survives_failed_source_and_deduplicates(tmp_path):
    def app(price):
        return create_app(Config(data_dir=tmp_path),start_scheduler=False,
                          adapters={'alternate':AlternateAdapter(Pages(price)),'broken':Broken()})
    with TestClient(app('19.90'),base_url='http://localhost') as c:
        watch=c.post('/api/watches',json={'name':'Synthetic radio','kind':'discovery',
                    'query':'Example Radio','source_ids':['alternate','broken'],'target_cents':2100}).json()
        wid=watch['id']
        result=c.post(f'/api/watches/{wid}/check',json={}).json()
        assert result['offers']==1 and 'broken' in result['error']
        state=c.get('/api/state').json()
        assert state['offers'][0]['total_cents']==2789 and state['events']==[]
        oid=state['offers'][0]['id']
    with TestClient(app('9.90'),base_url='http://localhost') as c:
        result=c.post(f'/api/watches/{wid}/check',json={}).json()
        assert result['offers']==1 and 'broken' in result['error']
        state=c.get('/api/state').json()
        assert state['offers'][0]['total_cents']==1789 and len(state['events'])==1
        assert [x['total_cents'] for x in c.get(f'/api/offers/{oid}/history').json()]==[2789,1789]
        c.post(f'/api/watches/{wid}/check',json={})
        assert len(c.get('/api/state').json()['events'])==1
        assert len(c.get(f'/api/offers/{oid}/history').json())==2
