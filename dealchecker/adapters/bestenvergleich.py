"""Authorized discovery/reference API; no seller, shipping or retail-stock claim."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
import re
from time import monotonic, time
from urllib.parse import urlencode, urlsplit

from dealchecker.models import Offer, WatchInput
from .base import Adapter, safe_https_url

_HOSTS = {'bestenvergleich.de'}
_BASE = 'https://bestenvergleich.de/api/v1'
_PATH = re.compile(r'/[a-z0-9-]+/[a-z0-9-]+/?\Z')


def _ean(value):
    if value in (None, ''):
        return ''
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12,14})', value):
        raise ValueError('Bestenvergleich liefert eine ungültige EAN')
    digits = [int(c) for c in value]
    if (sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1]))) + digits[-1]) % 10:
        raise ValueError('Bestenvergleich liefert eine ungültige EAN-Prüfziffer')
    return value


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('Bestenvergleich-Preis ist ungültig')
    try:
        amount = Decimal(str(value)) * 100
    except InvalidOperation:
        raise ValueError('Bestenvergleich-Preis ist ungültig') from None
    if not amount.is_finite() or amount < 0 or amount > 1_000_000_000_000 or amount != amount.to_integral_value():
        raise ValueError('Bestenvergleich-Preis ist ungültig')
    return int(amount)


def _text(value, *, optional=False):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or len(value) > 300 or (not optional and not value.strip()):
        raise ValueError('Bestenvergleich-Produktdaten sind ungültig')
    return value.strip()


def _url(value):
    safe_https_url(value, hosts=_HOSTS, no_query=True)
    if not _PATH.fullmatch(urlsplit(value).path):
        raise ValueError('Bestenvergleich-URL ist keine Produktseite')
    return value


class BestenvergleichAdapter(Adapter):
    id = 'bestenvergleich'
    name = 'Bestenvergleich · Preisreferenzen'
    description = ('Elektronik, Haushalt und Küche: Preisreferenzen ohne Händler-/Versandnachweis; '
                   'keine Gesamtpreisalarme. Quellenlinks können Affiliate-Links enthalten.')
    min_interval_seconds = 1200  # Publisher reports a 20-minute refresh cycle.

    def __init__(self, client):
        self.client = client
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[float, list[Offer]]] = OrderedDict()

    @staticmethod
    def _offer(row, observed):
        if not isinstance(row, dict):
            raise ValueError('Bestenvergleich liefert kein Produktobjekt')
        product_id, count = row.get('id'), row.get('offer_count')
        if (type(product_id) is not int or not 0 < product_id < 10**20
                or type(count) is not int or count < 0):
            raise ValueError('Bestenvergleich-Produktidentität oder Angebotszahl ist ungültig')
        if row.get('price_min') is None or count == 0:
            return None  # A missing price is never a free or currently purchasable product.
        category, manufacturer = row.get('category') or {}, row.get('manufacturer') or {}
        if not isinstance(category, dict) or not isinstance(manufacturer, dict):
            raise ValueError('Bestenvergleich-Metadaten sind ungültig')
        return Offer(
            source=BestenvergleichAdapter.id, merchant='Händler nicht ausgewiesen',
            product_id=str(product_id), name=_text(row.get('name')), url=_url(row.get('url')),
            price_cents=_money(row['price_min']), shipping_cents=None, availability='unknown',
            checked_at=observed, gtin=_ean(row.get('ean')),
            manufacturer=_text(manufacturer.get('name'), optional=True),
            category=_text(category.get('name'), optional=True),
            provenance='reference', quality='aggregate_reference', offer_type='reference',
        )

    async def _request(self, path, *, search=False):
        async with self._lock:
            cached = self._cache.get(path)
            if cached and monotonic() < cached[0]:
                self._cache.move_to_end(path)
                return list(cached[1])
            data = await self.client.get_json(_BASE + path, allowed_hosts=_HOSTS)
            if not isinstance(data, dict):
                raise ValueError('Bestenvergleich-Antwort ist ungültig')
            rows = data.get('results') if search else [data]
            if not isinstance(rows, list) or (search and len(rows) > 25):
                raise ValueError('Bestenvergleich-Suchantwort ist ungültig oder zu groß')
            observed = time()
            offers = [offer for row in rows if (offer := self._offer(row, observed)) is not None]
            self._cache[path] = (monotonic() + self.min_interval_seconds, offers)
            self._cache.move_to_end(path)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
            return list(offers)

    async def _search(self, terms):
        offers = {}
        for term in dict.fromkeys(terms):
            if len(term.strip()) < 2:
                raise ValueError('Bestenvergleich benötigt mindestens zwei Zeichen je Suchbegriff')
            path = '/search?' + urlencode({'q': term, 'limit': 25})
            for offer in await self._request(path, search=True):
                offers.setdefault(offer.product_id, offer)
        return list(offers.values())

    async def search(self, watch: WatchInput):
        if watch.kind != 'discovery':
            raise ValueError('Bestenvergleich-Suche erfordert einen Suchauftrag')
        return await self._search([watch.query, *watch.synonyms])

    async def lookup(self, watch: WatchInput):
        if watch.kind != 'exact':
            raise ValueError('Bestenvergleich-Einzelabfrage erfordert eine exakte Überwachung')
        if watch.model or watch.variant:
            raise ValueError('Bestenvergleich kann Modell- oder Variantenfelder nicht verifizieren; Produkt-ID/EAN verwenden')
        if watch.product_id and not re.fullmatch('[1-9][0-9]{0,19}', watch.product_id):
            raise ValueError('Bestenvergleich-Produkt-ID muss numerisch sein')
        requested_ean = _ean(watch.gtin)
        if watch.url:
            _url(watch.url)
        reference = watch.product_id or requested_ean or (urlsplit(watch.url).path.strip('/') if watch.url else '')
        if reference:
            offers = await self._request('/products/' + reference)
            for offer in offers:
                if ((watch.product_id and offer.product_id != watch.product_id)
                        or (requested_ean and offer.gtin.zfill(14) != requested_ean.zfill(14))
                        or (watch.url and offer.url.rstrip('/') != watch.url.rstrip('/'))):
                    raise ValueError('Bestenvergleich-Produktidentität und Abfrage widersprechen sich')
            return offers
        title = watch.query or watch.name
        matches = [o for o in await self._search([title]) if o.name.casefold() == title.casefold()]
        if len(matches) != 1:
            raise ValueError('Bestenvergleich-Titel ist mehrdeutig oder nicht exakt gefunden; Produkt-ID auswählen')
        return matches
