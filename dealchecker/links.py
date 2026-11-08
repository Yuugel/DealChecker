"""Resolve only registered merchant hosts; never fetch an arbitrary pasted URL."""
import asyncio

from .models import Offer, WatchInput
from .network import validate_url

BUILTIN_HOSTS = {'apple': ('apps.apple.com',), 'modula24': ('modula24.com',),
                 'bestenvergleich': ('bestenvergleich.de',)}
UNSUPPORTED = {
    'amazon.de': 'Amazon ist in dieser Konfiguration nicht eingerichtet.',
    'amzn.eu': 'Bitte den vollständigen amazon.de-Produktlink verwenden. Amazon-Kurzlinks werden nicht aufgelöst.',
    'mediamarkt.de': 'MediaMarkt ist in dieser Konfiguration nicht eingerichtet.',
    'saturn.de': 'Saturn ist in dieser Konfiguration nicht eingerichtet.',
    'otto.de': 'Für automatische OTTO-Datennutzung fehlt die erforderliche Freigabe.',
    'kaufland.de': 'Kauflands Zugriffs- und robots-Regeln konnten wegen einer Zugriffssperre nicht verifiziert werden. Keine automatische Überwachung aktiviert.',
    'galaxus.de': 'Galaxus untersagt systematische automatisierte Abrufe ohne Zustimmung. Alternativ bietet der Händler eigene Preisbenachrichtigungen.',
}


def url_hosts(adapter):
    return tuple(getattr(adapter, 'url_hosts', BUILTIN_HOSTS.get(adapter.id, ())))


async def resolve_link(url: str, registry: dict, *, amazon_prime: bool = False) -> dict:
    host = validate_url(url)
    matches = [a for a in registry.values() if host in url_hosts(a)]
    if len(matches) != 1:
        raise ValueError(UNSUPPORTED.get(host.removeprefix('www.'),
                         'Dieser Händlerlink wird noch nicht unterstützt. Wähle eine verfügbare Quelle oder suche nach dem Produktnamen.'))
    adapter = matches[0]
    if not getattr(adapter, 'configured', True):
        raise ValueError('Für diesen Händler fehlt der ReefAPI-Zugang. Bitte unter Einstellungen hinterlegen.')
    watch = WatchInput(name='Produktlink', kind='exact', url=url, source_ids=[adapter.id], amazon_prime=amazon_prime)
    try:
        offers = await asyncio.wait_for(adapter.lookup(watch), timeout=60)
    except asyncio.TimeoutError:
        raise ValueError('Die Produktabfrage hat das Zeitlimit erreicht. Bitte später erneut versuchen.') from None
    if not isinstance(offers, list) or not 1 <= len(offers) <= 100:
        raise ValueError('Kein eindeutig prüfbares Produkt am angegebenen Link gefunden.')
    for offer in offers:
        if not isinstance(offer, Offer) or offer.source != adapter.id:
            raise ValueError('Die Quelle lieferte widersprüchliche Produktdaten.')
        validate_url(offer.url, set(url_hosts(adapter)))
    warnings = []
    if adapter.id == 'amazon' and amazon_prime and all(o.price_basis != 'prime' for o in offers):
        warnings.append('Kein eindeutig bestätigter Prime-Sonderpreis verfügbar; regulärer Preis angezeigt.')
    if any(o.offer_type == 'reference' for o in offers):
        warnings.append('Preisreferenz ohne bestätigtes Händlerangebot: kein Kaufpreisalarm.')
    if any(o.shipping_cents is None for o in offers):
        warnings.append('Versand unbekannt: kein bestätigter Gesamtpreis und kein Kaufpreisalarm.')
    if any(o.availability != 'in_stock' for o in offers):
        warnings.append('Mindestens ein Treffer ist nicht als lieferbar bestätigt.')
    return {'source_id': adapter.id, 'source_name': adapter.name, 'amazon_prime': amazon_prime,
            'offers': [o.model_dump() for o in offers], 'warnings': warnings}
