"""Synthetic API fixtures; never mistaken for real shops or delivered prices."""
import asyncio
import pytest

from dealchecker.adapters.bestenvergleich import BestenvergleichAdapter
from dealchecker.models import WatchInput


class Client:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def get_json(self, url, *, allowed_hosts=None):
        self.calls.append((url, allowed_hosts))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def product(id=1234, **changes):
    row = dict(id=id, name='Synthetic Airfryer', url=f'https://bestenvergleich.de/fritteusen/synthetic-{id}',
               price_min=39.99, offer_count=3, ean='4006381333931',
               category={'name': 'Fritteusen'}, manufacturer={'name': 'Synthetic'})
    row.update(changes)
    return row


def watch(**changes):
    return WatchInput(**dict(dict(name='Synthetic search', kind='discovery', query='Airfryer',
                                 source_ids=['bestenvergleich']), **changes))


async def test_search_is_reference_not_a_retailer_or_shipping_claim():
    c = Client({'results': [product(), product(2345, price_min=None), product(3456, offer_count=0)]})
    rows = await BestenvergleichAdapter(c).search(watch())
    assert len(rows) == 1
    o = rows[0]
    assert o.offer_type == 'reference' and o.provenance == 'reference'
    assert o.merchant == 'Händler nicht ausgewiesen' and o.seller == ''
    assert o.price_cents == 3999 and o.shipping_cents is None and o.total_cents is None
    assert o.availability == 'unknown'
    assert o.gtin == '4006381333931'
    assert o.url == product()['url']
    assert c.calls[0][1] == {'bestenvergleich.de'}


async def test_exact_id_ean_and_url_use_targeted_endpoint():
    for identity, suffix in [({'product_id': '1234'}, '1234'),
                             ({'gtin': '4006381333931'}, '4006381333931'),
                             ({'url': product()['url']}, 'fritteusen/synthetic-1234')]:
        c = Client(product())
        rows = await BestenvergleichAdapter(c).lookup(watch(kind='exact', query='', **identity))
        assert len(rows) == 1 and rows[0].product_id == '1234'
        assert c.calls[0][0].endswith('/products/' + suffix)


async def test_identity_conflict_and_unverified_variant_or_model_rejected():
    for identity in [dict(product_id='5678'), dict(gtin='5901234123457'),
                     dict(product_id='1234', url='https://bestenvergleich.de/fritteusen/other')]:
        with pytest.raises(ValueError, match='Identität|widersprechen'):
            await BestenvergleichAdapter(Client(product())).lookup(watch(kind='exact', **identity))
    for identity in [dict(variant='blue'), dict(model='ABC')]:
        with pytest.raises(ValueError, match='verifizieren'):
            await BestenvergleichAdapter(Client()).lookup(watch(kind='exact', **identity))


async def test_cache_singleflight_does_not_redate_and_refreshes_changed_reference(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('dealchecker.adapters.bestenvergleich.monotonic', lambda: clock[0])
    monkeypatch.setattr('dealchecker.adapters.bestenvergleich.time', lambda: clock[0])
    c = Client(product(), product(price_min=29.99))
    adapter = BestenvergleichAdapter(c)
    exact = watch(kind='exact', product_id='1234')
    a, b = await asyncio.gather(adapter.lookup(exact), adapter.lookup(exact))
    assert len(c.calls) == 1 and a[0].checked_at == b[0].checked_at == 100
    clock[0] = 200
    assert (await adapter.lookup(exact))[0].checked_at == 100
    clock[0] = 1301
    newer = (await adapter.lookup(exact))[0]
    assert newer.price_cents == 2999 and newer.checked_at == 1301 and len(c.calls) == 2


@pytest.mark.parametrize('changes', [dict(price_min=-1), dict(price_min=True), dict(price_min='NaN'),
                                    dict(price_min=3.999), dict(url='https://evil.example/item'),
                                    dict(id=True), dict(offer_count=-1), dict(ean='123')])
async def test_malformed_prices_identity_and_urls_fail_closed(changes):
    with pytest.raises(ValueError):
        await BestenvergleichAdapter(Client({'results': [product(**changes)]})).search(watch())


async def test_synonyms_deduplicate_and_exact_title_is_not_fuzzy():
    c = Client({'results': [product()]}, {'results': [product(), product(2345)]})
    assert len(await BestenvergleichAdapter(c).search(watch(synonyms=['Fritteuse']))) == 2
    with pytest.raises(ValueError, match='mehrdeutig'):
        await BestenvergleichAdapter(Client({'results': [product(), product(2345)]})).lookup(
            watch(kind='exact', query='Synthetic Airfryer'))
    with pytest.raises(ValueError, match='gefunden'):
        await BestenvergleichAdapter(Client({'results': [product()]})).lookup(watch(kind='exact', query='Airfryer'))


async def test_failure_does_not_return_old_cached_data_as_fresh(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('dealchecker.adapters.bestenvergleich.monotonic', lambda: clock[0])
    adapter = BestenvergleichAdapter(Client(product(), ValueError('source unavailable')))
    exact = watch(kind='exact', product_id='1234')
    await adapter.lookup(exact)
    clock[0] = 1400
    with pytest.raises(ValueError, match='unavailable'):
        await adapter.lookup(exact)
