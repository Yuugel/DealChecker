"""Conservative, bounded access to public German shop HTML."""
from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from html.parser import HTMLParser
from time import monotonic, time
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

from dealchecker.identity import canonical_gtin
from dealchecker.models import Offer
from dealchecker.network import MAX_BODY, validate_url

from .base import Adapter


_ROBOTS_TTL = 6 * 60 * 60
_ERROR_TTL = 30 * 60
_PERCENT = re.compile(r"%([0-9a-fA-F]{2})")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_CENTS = re.compile(r"[0-9]{1,10}(?:\.[0-9]{1,2})?")
_WORDS = re.compile(r"[^\W_]+")
_MODEL = re.compile(r"(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._/-]{2,99}")
_DELAY = re.compile(r"[0-9]{1,5}(?:\.[0-9]{1,3})?")
_SHIPPING_FIELDS = {"@type", "shippingDestination", "shippingRate", "deliveryTime"}
_MAX_DETAILS = 6
_MAX_CACHE = 96


def _escaped_path(value: str) -> str:
    def normalize(match):
        char = chr(int(match[1], 16))
        return char if char in _UNRESERVED else match[0].upper()

    return _PERCENT.sub(normalize, quote(value, safe="/?:@!$&'()*+,;=%[]"))


def _robots_rules(text: str):
    groups = []
    agents, rules = [], []
    seen_rule = False
    delay = 0.0
    for line in text.lstrip("\ufeff").splitlines():
        name, colon, value = line.split("#", 1)[0].partition(":")
        if not colon:
            continue
        name, value = name.strip().lower(), value.strip()
        if name == "user-agent" and re.fullmatch(r"[A-Za-z_-]+|\*", value):
            if seen_rule:
                groups.append((agents, rules, delay))
                agents, rules, seen_rule, delay = [], [], False, 0.0
            agents.append(value.lower())
        elif agents and name in {"allow", "disallow"}:
            seen_rule = True
            if not value:
                continue
            if not value.startswith(("/", "*")) or len(value) > 2048:
                raise ValueError("Ungültige Regel in robots.txt")
            end_anchor = value.endswith("$")
            pattern = _escaped_path(value[:-1] if end_anchor else value)
            literal = pattern.replace("*", "")
            specificity = len(literal) - 2 * len(_PERCENT.findall(literal))
            # Collapse repeated wildcards and avoid regex backtracking across long URLs.
            parts = tuple(part for part in pattern.split("*") if part)
            rules.append((specificity, name == "allow", parts, pattern.startswith("*"),
                          pattern.endswith("*"), end_anchor))
        elif agents and name == "crawl-delay":
            seen_rule = True
            if not _DELAY.fullmatch(value):
                raise ValueError("Ungültiger Crawl-delay in robots.txt")
            delay = max(delay, float(value))
    if agents:
        groups.append((agents, rules, delay))
    if not groups:
        raise ValueError("Keine gültige Richtlinie in robots.txt")
    specific = [(rules, delay) for agents, rules, delay in groups if "dealchecker" in agents]
    applicable = specific or [(rules, delay) for agents, rules, delay in groups if "*" in agents]
    return tuple(rule for rules, _ in applicable for rule in rules), max(
        (delay for _, delay in applicable), default=0.0,
    )


def _matches_rule(path: str, parts, leading_star: bool, trailing_star: bool, anchored: bool) -> bool:
    if not parts:
        return True
    if not leading_star and not path.startswith(parts[0]):
        return False
    if anchored and not trailing_star:
        if not path.endswith(parts[-1]):
            return False
        if len(parts) == 1 and not leading_star:
            return path == parts[0]
        path = path[:-len(parts[-1])]
        parts = parts[:-1]
    position = 0
    for part in parts:
        start = path.find(part, position)
        if start < 0:
            return False
        position = start + len(part)
    return True


class PublicPageClient:
    """Check each network read against the site's live robots policy."""

    def __init__(self, client, hosts):
        self.client = client
        self.hosts = set(hosts)
        self._robots = {}
        self._locks = {}
        self._last_read = {}

    async def _read(self, url: str, host: str, max_body: int, delay: float) -> str:
        while (wait := self._last_read.get(host, -delay) + delay - monotonic()) > 0:
            await asyncio.sleep(wait)
        try:
            return await self.client.get_text(url, allowed_hosts={host}, max_body=max_body)
        finally:
            self._last_read[host] = monotonic()

    async def _rules_for(self, host: str):
        expires, rules, delay = self._robots.get(host, (0, None, 0.0))
        if monotonic() >= expires:
            try:
                robots = await self._read(f"https://{host}/robots.txt", host, 256 * 1024, delay)
                rules, delay = _robots_rules(robots)
            except ValueError as error:
                self._robots[host] = (monotonic() + _ERROR_TTL, None, delay)
                raise ValueError("robots.txt nicht verifizierbar; Quelle pausiert") from error
            self._robots[host] = (monotonic() + _ROBOTS_TTL, rules, delay)
        if rules is None:
            raise ValueError("robots.txt oder Website-Zugriff gesperrt; Quelle pausiert")
        return rules, delay

    async def get_page(self, url: str, *, max_body: int = MAX_BODY) -> str:
        host = validate_url(url, self.hosts)
        parsed = urlsplit(url)
        decoded_path = unquote(parsed.path, errors="strict")
        if ("\\" in decoded_path or decoded_path.count("/") != parsed.path.count("/")
                or any(part in {".", ".."} for part in decoded_path.split("/"))):
            raise ValueError("Mehrdeutiger URL-Pfad")
        path = _escaped_path((parsed.path or "/") + ("?" + parsed.query if parsed.query else ""))
        async with self._locks.setdefault(host, asyncio.Lock()):
            rules, delay = await self._rules_for(host)
            matches = ((length, allow) for length, allow, *pattern in rules if _matches_rule(path, *pattern))
            if not max(matches, default=(0, True))[1]:
                raise ValueError("robots.txt sperrt diese Adresse")
            try:
                return await self._read(url, host, max_body, delay)
            except ValueError as error:
                if "HTTP 403" in str(error):
                    self._robots[host] = (monotonic() + _ERROR_TTL, None, delay)
                raise


class _PageMarkup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.scripts = []
        self._script = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script":
            self._script = attributes.get("type") == "application/ld+json"
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes)

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = False

    def handle_data(self, data):
        if self._script:
            self.scripts.append(data)


def _markup(html: str) -> _PageMarkup:
    page = _PageMarkup()
    page.feed(html)
    page.close()
    return page


def jsonld_documents(html: str) -> list[dict]:
    result = []
    for script in _markup(html).scripts:
        try:
            value = json.loads(script)
        except (ValueError, RecursionError):
            continue
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict):
                result.append(entry)
                graph = entry.get("@graph")
                if isinstance(graph, list):
                    result.extend(node for node in graph if isinstance(node, dict))
    return result


def _type(value: object) -> str:
    return value.removeprefix("https://schema.org/").removeprefix("http://schema.org/") if isinstance(value, str) else ""


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _money(value: object) -> int:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("Händlerpreis fehlt")
    if not _CENTS.fullmatch(text := str(value)):
        raise ValueError("Händlerpreis ist kein gültiger EUR-Betrag")
    euro, _, fraction = text.partition(".")
    return int(euro) * 100 + int(fraction.ljust(2, "0"))


def _shipping(offer: dict) -> int | None:
    if {"eligibleQuantity", "eligibleTransactionVolume"} & offer.keys():
        return None
    entries = offer.get("shippingDetails")
    entries = entries if isinstance(entries, list) else [entries]
    matches = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        destination, rate = entry.get("shippingDestination"), entry.get("shippingRate")
        if isinstance(destination, dict) and destination.get("addressCountry") == "DE":
            if (entry.keys() - _SHIPPING_FIELDS or destination.keys() - {"@type", "addressCountry"}
                    or not isinstance(rate, dict) or rate.keys() - {"@type", "currency", "value"}
                    or rate.get("currency") != "EUR"):
                return None
            try:
                matches.append(_money(rate.get("value")))
            except ValueError:
                return None
    return matches[0] if len(matches) == 1 else None


def _words(value: str) -> set[str]:
    return set(_WORDS.findall(value.casefold()))


def _includes(value: str, term: str) -> bool:
    words = _words(term)
    return bool(words) and words <= _words(value)


def _search_term(term: str) -> str:
    if canonical_gtin(term):
        digits = re.sub(r"[ -]", "", term)
        # German shop search commonly indexes an EAN-13 without the GTIN-14 padding.
        return digits[1:] if len(digits) == 14 and digits.startswith("0") else digits
    return term


def _matches_term(offer: Offer, term: str) -> bool:
    if gtin := canonical_gtin(term):
        return canonical_gtin(offer.gtin) == gtin
    return offer.model.casefold() == term.casefold() or _includes(f"{offer.name} {offer.variant}", term)


def _gtin(product: dict, page_gtin: str) -> str:
    values = [_text(product.get(key)) for key in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14")]
    found = {}
    for value in [*values, page_gtin]:
        if value and (canonical := canonical_gtin(value)):
            found[canonical] = value
    if len(found) > 1:
        raise ValueError("Händlerseite enthält widersprüchliche GTIN")
    return next(iter(found.values()), "")


class PublicShopAdapter(Adapter):
    """Use the public search form for discovery, then read bounded product pages."""

    supports_search = True
    min_interval_seconds = 1800
    search_path: str
    search_parameter: str
    listing_link_class: str
    url_hosts: tuple[str, ...]
    product_path: re.Pattern
    seller_name: str

    def __init__(self, client):
        self.origin = f"https://{self.url_hosts[0]}"
        self.pages = PublicPageClient(client, self.url_hosts)
        self._offers = OrderedDict()
        self._searches = OrderedDict()
        self._lock = asyncio.Lock()

    def _product_url(self, value: str) -> str:
        validate_url(value, set(self.url_hosts))
        parts = urlsplit(value)
        decoded_path = unquote(parts.path, errors="strict")
        if (parts.query or parts.netloc != self.url_hosts[0] or not self.product_path.fullmatch(decoded_path)
                or any(segment in {".", ".."} for segment in decoded_path.split("/"))
                or decoded_path.count("/") != parts.path.count("/") or len(value) > 1000):
            raise ValueError("Adresse ist keine öffentliche Produktseite dieser Quelle")
        return self.origin + quote(decoded_path, safe="/-._~")

    def _page_fields(self, html: str, product: dict, url: str) -> tuple[str, str, str]:
        """Return stable product ID, variant, and GTIN from source-specific markup."""
        raise NotImplementedError

    def _parse_offer(self, html: str, url: str) -> Offer:
        products = [item for item in jsonld_documents(html) if _type(item.get("@type")) == "Product"]
        if len(products) != 1:
            raise ValueError("Produktseite liefert kein eindeutiges Produkt")
        product = products[0]
        offers = product.get("offers")
        offers = offers if isinstance(offers, list) else [offers]
        if len(offers) != 1 or not isinstance(offers[0], dict) or _type(offers[0].get("@type")) != "Offer":
            raise ValueError("Produktseite liefert kein eindeutiges Händlerangebot")
        offer = offers[0]
        if offer.get("priceCurrency") != "EUR" or self._product_url(offer.get("url")) != url:
            raise ValueError("Produktlink oder Währung widerspricht dem Händlerangebot")
        seller = offer.get("seller")
        if not isinstance(seller, dict) or _text(seller.get("name")) != self.seller_name:
            raise ValueError("Produktseite liefert keinen bestätigten Händler")
        product_id, variant, page_gtin = self._page_fields(html, product, url)
        if not product_id:
            raise ValueError("Produktseite liefert keine stabile Artikelnummer")
        brand = product.get("brand")
        brand = _text(brand.get("name")) if isinstance(brand, dict) else _text(brand)
        name = _text(product.get("name"))
        if not name:
            raise ValueError("Produktseite liefert keinen Produktnamen")
        if brand and not name.casefold().startswith(brand.casefold()):
            name = f"{brand} {name}"
        availability = _type(offer.get("availability"))
        if availability in {"InStock", "LimitedAvailability", "OnlineOnly"}:
            availability = "in_stock"
        elif availability in {"OutOfStock", "SoldOut", "Discontinued"}:
            availability = "out_of_stock"
        else:
            availability = "unknown"
        condition = {"NewCondition": "new", "UsedCondition": "used",
                     "RefurbishedCondition": "refurbished"}.get(_type(offer.get("itemCondition")), "unknown")
        return Offer(
            source=self.id, merchant=self.seller_name, seller=self.seller_name,
            product_id=product_id, name=name, url=url, variant=variant,
            gtin=_gtin(product, page_gtin), model=_text(product.get("mpn")), manufacturer=brand,
            category=_text(product.get("category")), price_cents=_money(offer.get("price")),
            shipping_cents=_shipping(offer), availability=availability, condition=condition, checked_at=time(),
        )

    def _store(self, cache: OrderedDict, key: str, value):
        cache[key] = (monotonic() + self.min_interval_seconds, value)
        cache.move_to_end(key)
        if len(cache) > _MAX_CACHE:
            cache.popitem(last=False)
        return value

    async def _offer_at(self, url: str) -> Offer:
        expires, offer = self._offers.get(url, (0, None))
        if monotonic() >= expires:
            return self._store(self._offers, url, self._parse_offer(await self.pages.get_page(url), url))
        return offer

    async def _links(self, term: str) -> list[tuple[str, str]]:
        expires, links = self._searches.get(term, (0, None))
        if monotonic() < expires:
            return links
        url = self.origin + self.search_path + "?" + urlencode({self.search_parameter: term})
        html = await self.pages.get_page(url)
        if re.search(r"<title[^>]*>\s*(?:Just a moment|Security Check|Access Denied)\b", html, re.I):
            raise ValueError("Händlerseite meldet eine Zugriffssperre")
        links = {}
        for link in _markup(html).links:
            if self.listing_link_class not in (link.get("class") or "").split():
                continue
            try:
                product_url = self._product_url(urljoin(self.origin, link["href"]))
            except ValueError:
                continue
            description = _text(link.get("title")) or unquote(urlsplit(product_url).path).replace("-", " ")
            links.setdefault(product_url, description)
        return self._store(self._searches, term, list(links.items())[:100])

    async def _find(self, terms: list[str], *, exact: bool = False, identity: bool = False) -> list[Offer]:
        candidates = {}
        for term in dict.fromkeys(terms):
            links = await self._links(_search_term(term))
            matching_links = [url for url, description in links if _includes(description, term)]
            if identity or canonical_gtin(term) or (not matching_links and _MODEL.fullmatch(term)):
                matching_links = [url for url, _ in links]
            candidates.update(dict.fromkeys(matching_links))
        if exact and len(candidates) > _MAX_DETAILS:
            raise ValueError("Zu viele mögliche Treffer; bitte direkten Produktlink verwenden")
        offers = []
        for url in list(candidates)[:_MAX_DETAILS]:
            offer = await self._offer_at(url)
            if identity or any(_matches_term(offer, term) for term in terms):
                offers.append(offer)
        return offers

    async def search(self, watch) -> list[Offer]:
        if watch.kind != "discovery" or not watch.query:
            raise ValueError("Händlersuche erfordert einen Suchbegriff")
        async with self._lock:
            return await self._find([watch.query, *watch.synonyms])

    async def lookup(self, watch) -> list[Offer]:
        if watch.kind != "exact":
            raise ValueError("Händlerabfrage erfordert eine exakte Produktüberwachung")
        gtin = canonical_gtin(watch.gtin) if watch.gtin else ""
        if watch.gtin and not gtin:
            raise ValueError("GTIN ist ungültig")
        async with self._lock:
            if watch.url:
                offers = [await self._offer_at(self._product_url(watch.url))]
            elif watch.product_id:
                raise ValueError("Für diese Artikelnummer wird der direkte Produktlink benötigt")
            else:
                offers = await self._find([watch.gtin or watch.model or watch.query or watch.name], exact=True,
                                          identity=bool(gtin or watch.model))
        matches = [
            offer for offer in offers
            if (not gtin or (offer.gtin and canonical_gtin(offer.gtin) == gtin))
            and (not watch.product_id or offer.product_id.casefold() == watch.product_id.casefold())
            and (not watch.model or offer.model.casefold() == watch.model.casefold())
            and (not watch.variant or offer.variant.casefold() == watch.variant.casefold())
            and (watch.url or gtin or watch.model or offer.name.casefold() == (watch.query or watch.name).casefold())
        ]
        if watch.url and not matches:
            raise ValueError("Produktidentität widerspricht der Überwachung")
        if len(matches) > 1:
            raise ValueError("Produktsuche ist mehrdeutig; bitte direkten Produktlink verwenden")
        return matches
