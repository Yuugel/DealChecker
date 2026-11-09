"""Read ordinary Amazon.de product and search pages without a login."""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from time import monotonic, time
from urllib.parse import parse_qs, urlencode, unquote, urlsplit

from dealchecker.identity import canonical_gtin
from dealchecker.models import Offer

from .base import safe_https_url
from .public_pages import ProductDetailFailure, PublicShopAdapter
from .reef import _asin


_MAX_BODY = 4 * 1024 * 1024
_ASIN_PATH = re.compile(r"/(?:(?:[^/]+/)?dp|gp/product)/([A-Z0-9]{10})(?:/ref=[^/]+(?:/[0-9-]+)?)?/?", re.I)
_WHOLE = re.compile(r"(?:[0-9]{1,10}|[0-9]{1,3}(?:\.[0-9]{3}){1,3}),?")
_GERMAN_MONEY = re.compile(r"([0-9]{1,10}|[0-9]{1,3}(?:\.[0-9]{3}){1,3}),([0-9]{2})\s*€")
_MERCHANT_ID = re.compile(r"[A-Z0-9]{6,30}")
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_NON_CONTENT = {"script", "style", "template", "noscript"}
_CONDITIONAL = re.compile(r"prime(?:-?exclusive|savings|only)|business(?:price|buybox)|subscribe|coupon|sparabo|renewed|usedaccordion", re.I)
_OTHER_CONDITIONS = re.compile(r"business(?:price|buybox)|subscribe|coupon|sparabo|renewed|usedaccordion", re.I)
_CAPTCHA = re.compile(r"<title[^>]*>\s*(?:Robot Check|Amazon CAPTCHA|Access Denied)\b|<form\b[^>]*action=[\"'](?:https://www\.amazon\.de)?/errors/validateCaptcha", re.I)
_COLOR_ROW = "inline-twister-row-"
_PRIMARY_DELIVERY = "mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE"


@dataclass(slots=True)
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    parent: _Node | None = None
    children: list[_Node | str] = field(default_factory=list)

    def nodes(self):
        pending = [self]
        while pending:
            item = pending.pop()
            if isinstance(item, _Node):
                yield item
                pending.extend(reversed(item.children))

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def text(self) -> str:
        parts = []
        pending = [self]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                parts.append(item)
            else:
                pending.extend(reversed(item.children))
        return " ".join("".join(parts).split())

    def has_class(self, value: str) -> bool:
        return value in (self.attrs.get("class") or "").split()

    def by_id(self, value: str) -> list[_Node]:
        return [node for node in self.nodes() if node.attrs.get("id") == value]


class _DOM(HTMLParser):
    def __init__(self, page: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("")
        self.stack = [self.root]
        self.feed(page)
        self.close()

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1]
        node = _Node(tag, dict(attrs), parent)
        if parent.tag not in _NON_CONTENT:
            parent.children.append(node)
        if tag not in _VOID:
            if len(self.stack) >= 256:
                raise ValueError("Amazon-Seite ist zu tief verschachtelt")
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if self.stack[-1].tag not in _NON_CONTENT and data:
            self.stack[-1].children.append(data)


def _only(nodes: list[_Node], field_name: str) -> _Node:
    if len(nodes) != 1:
        raise ValueError(f"Amazon-Seite liefert kein eindeutiges Feld für {field_name}")
    return nodes[0]


def _conditional(node: _Node, *, prime: bool = False) -> bool:
    pattern = _OTHER_CONDITIONS if prime else _CONDITIONAL
    return any(pattern.search(" ".join(
        member.attrs.get(field_name) or "" for field_name in ("id", "class", "data-a-accordion-row-name")
    )) for member in (node, *node.ancestors()))


def _hidden_value(form: _Node, name: str) -> str:
    fields = [node for node in form.nodes()
              if node.tag == "input" and node.attrs.get("type") == "hidden" and node.attrs.get("name") == name]
    return _only(fields, name).attrs.get("value", "")


def _buybox(product: _Node, asin: str) -> tuple[_Node, str]:
    buybox = _only(product.by_id("desktop_buybox"), "Kaufbox")
    candidates = [form for form in buybox.by_id("addToCart")
                  if form.tag == "form" and form.by_id("desktop_qualifiedBuyBox") and not _conditional(form)]
    form = _only(candidates, "öffentliche Kaufoption")
    box = _only(form.by_id("desktop_qualifiedBuyBox"), "Angebot")
    if (box.attrs.get("data-csa-c-asin") != asin or _hidden_value(form, "ASIN") != asin
            or _hidden_value(form, "isMerchantExclusive") != "0"):
        raise ValueError("Amazon-Kaufbox bestätigt ASIN oder öffentlichen Preis nicht")
    if any(node.tag == "input" and node.attrs.get("name") in {"isAddon", "usePrimeHandler"}
           and node.attrs.get("value") != "0" for node in form.nodes()):
        raise ValueError("Amazon-Kaufangebot ist an zusätzliche Bedingungen geknüpft")
    cart = _only(box.by_id("add-to-cart-button"), "Warenkorb-Schaltfläche")
    if "disabled" in cart.attrs or cart.attrs.get("aria-disabled") == "true":
        raise ValueError("Amazon-Kaufangebot ist deaktiviert")
    merchant_id = _hidden_value(form, "merchantID")
    if not _MERCHANT_ID.fullmatch(merchant_id):
        raise ValueError("Amazon-Kaufbox liefert keine Händler-ID")
    return box, merchant_id


def _seller(box: _Node, asin: str, merchant_id: str) -> str:
    feature = _only(box.by_id("merchantInfoFeature_feature_div"), "Verkäufer")
    if feature.attrs.get("data-csa-c-asin") != asin:
        raise ValueError("Amazon-Verkäuferdaten gehören nicht zur angefragten ASIN")
    links = feature.by_id("sellerProfileTriggerId")
    if links:
        names = set()
        for link in links:
            params = parse_qs(urlsplit(link.attrs.get("href") or "").query)
            if params.get("seller") != [merchant_id] or params.get("asin") != [asin]:
                raise ValueError("Amazon-Verkäufer widerspricht der Kaufbox")
            names.add(link.text())
        if len(names) != 1:
            raise ValueError("Amazon-Kaufbox liefert widersprüchliche Verkäufernamen")
        seller = names.pop()
    else:
        nodes = [node for node in feature.nodes() if node.has_class("offer-display-feature-text-message")]
        seller = _only(nodes, "Verkäufername").text()
    if not seller or seller.casefold() in {"verkäufer", "verkauf durch", "sold by"}:
        raise ValueError("Amazon-Kaufbox liefert keinen bestätigten Verkäufer")
    return seller[:200]


def _price(scope: _Node, asin: str, *, prime: bool = False) -> int:
    blocks = [node for node in scope.by_id("corePriceDisplay_desktop_feature_div") if not _conditional(node, prime=prime)]
    block = _only(blocks, "Artikelpreis")
    if block.attrs.get("data-csa-c-asin") != asin:
        raise ValueError("Amazon-Preis gehört nicht zur angefragten ASIN")
    prices = [node for node in block.nodes() if node.has_class("priceToPay") and not _conditional(node, prime=prime)]
    price = _only(prices, "Artikelpreis")

    def price_part(css_class: str) -> str:
        return _only([node for node in price.nodes() if node.has_class(css_class)], css_class).text()

    whole, fraction, symbol = (price_part(name) for name in ("a-price-whole", "a-price-fraction", "a-price-symbol"))
    if symbol != "€" or not _WHOLE.fullmatch(whole) or not re.fullmatch("[0-9]{2}", fraction):
        raise ValueError("Amazon-Artikelpreis ist nicht eindeutig in EUR ausgezeichnet")
    cents = int(whole.rstrip(",").replace(".", "")) * 100 + int(fraction)
    if cents <= 0:
        raise ValueError("Amazon liefert keinen positiven Artikelpreis")
    return cents


def _shipping(box: _Node, price_cents: int) -> int | None:
    candidates = box.by_id(_PRIMARY_DELIVERY)
    if len(candidates) != 1:
        return None
    options = [node for node in candidates[0].nodes() if "data-csa-c-delivery-price" in node.attrs]
    if len(options) != 1:
        return None
    option = options[0]
    attrs = option.attrs
    if (attrs.get("data-csa-c-delivery-condition") != ""
            or attrs.get("data-csa-c-mir-type") != "DELIVERY"
            or attrs.get("data-csa-c-mir-variant") != "DEFAULT"
            or attrs.get("data-csa-c-mir-sub-type") != ""
            or attrs.get("data-csa-c-delivery-benefit-program-id") not in {"", "cfs"}):
        return None
    price = attrs.get("data-csa-c-delivery-price", "")
    label = option.text()
    if re.search(r"\b(?:für|ab|bei|wenn|nur|prime|mitglied\w*|mindestbestell\w*|erstbestell\w*)\b", label, re.I):
        return None
    if (price == "GRATIS" and label.startswith("GRATIS Lieferung ")
            and (attrs.get("data-csa-c-delivery-benefit-program-id") != "cfs" or price_cents >= 3900)):
        return 0
    amount = _GERMAN_MONEY.fullmatch(price)
    if amount and price in label:
        return int(amount[1].replace(".", "")) * 100 + int(amount[2])
    return None


def _availability(box: _Node, asin: str) -> str:
    blocks = box.by_id("availabilityInsideBuyBox_feature_div")
    if len(blocks) != 1 or blocks[0].attrs.get("data-csa-c-asin") != asin:
        return "unknown"
    messages = [node.text().casefold() for node in blocks[0].nodes() if node.has_class("primary-availability-message")]
    if len(messages) != 1:
        return "unknown"
    message = messages[0]
    if message in {"auf lager", "in stock"} or re.fullmatch(r"nur noch [1-9][0-9]* auf lager[.!]?", message):
        return "in_stock"
    if message in {"derzeit nicht verfügbar.", "derzeit nicht verfügbar", "nicht auf lager", "out of stock"}:
        return "out_of_stock"
    return "unknown"


def _variant(product: _Node, asin: str) -> str:
    values = []
    for row in product.nodes():
        identifier = row.attrs.get("id") or ""
        if not identifier.startswith(_COLOR_ROW):
            continue
        dimension = identifier.removeprefix(_COLOR_ROW)
        label = _only(row.by_id("inline-twister-expanded-dimension-text-" + dimension), "gewählte Variante")
        selected = [node for node in row.nodes() if node.tag == "li"
                    and node.attrs.get("data-initiallyselected") == "true"]
        if _only(selected, "gewählte Variante").attrs.get("data-asin") != asin or not label.text():
            raise ValueError("Amazon-Variante widerspricht der angefragten ASIN")
        values.append(label.text())
    return "; ".join(values)


def _details(root: _Node, asin: str) -> dict[str, str]:
    facts = {}
    for details_id in ("detailBullets_feature_div", "productDetails_techSpec_section_1",
                       "productDetails_expanderSectionTables"):
        for section in root.by_id(details_id):
            context = next((node.attrs["data-csa-c-asin"] for node in (section, *section.ancestors())
                            if node.attrs.get("data-csa-c-asin")), None)
            if context not in (None, asin):
                continue
            for row in section.nodes():
                if row.tag == "tr":
                    cells = [node for node in row.children if isinstance(node, _Node) and node.tag in {"th", "td"}]
                elif row.tag == "li":
                    cells = [child for child in row.children if isinstance(child, _Node) and child.has_class("a-list-item")]
                    cells = [child for cell in cells for child in cell.children if isinstance(child, _Node)]
                else:
                    continue
                if len(cells) == 2:
                    label = cells[0].text().replace("\u200e", "").replace("\u200f", "").strip(" :").casefold()
                    value = cells[1].text().replace("\u200e", "").replace("\u200f", "").strip()
                    if value:
                        facts.setdefault(label, value)
    return facts


class AmazonAdapter(PublicShopAdapter):
    id = "amazon"
    name = "Amazon DE"
    description = "Öffentliche Amazon.de-Produktsuche und Direktlinks ohne Konto, optional ausgewiesener Prime-Preis"
    url_hosts = ("www.amazon.de", "amazon.de")
    search_path = "/s"
    search_parameter = "k"

    def __init__(self, client):
        super().__init__(client)
        self._blocked_until = 0.0
        self._prime = False

    def _product_url(self, value: str) -> str:
        safe_https_url(value, hosts=set(self.url_hosts))
        parsed = urlsplit(value)
        path = unquote(parsed.path, errors="strict")
        if (path.count("/") != parsed.path.count("/") or "\\" in path
                or any(part in {".", ".."} for part in path.split("/"))
                or any(ord(char) < 32 for char in path) or not (match := _ASIN_PATH.fullmatch(path))):
            raise ValueError("Amazon-Link ist keine direkte deutsche Produktseite")
        if "smid" in parse_qs(parsed.query, keep_blank_values=True):
            raise ValueError("Amazon-Händlerparameter wird im direkten Kaufangebot nicht bestätigt")
        return f"{self.origin}/dp/{_asin(match[1])}"

    async def _page(self, url: str) -> str:
        if monotonic() < self._blocked_until:
            raise ValueError("Amazon-Zugriff vorübergehend pausiert")
        page = await self.pages.get_page(url, max_body=_MAX_BODY)
        if _CAPTCHA.search(page):
            self._blocked_until = monotonic() + self.min_interval_seconds
            raise ValueError("Amazon meldet eine Zugriffssperre")
        return page

    async def _offer_at(self, url: str) -> Offer:
        key = (url, self._prime)
        expires, offer = self._offers.get(key, (0, None))
        if monotonic() < expires:
            return offer
        try:
            page = await self._page(url)
        except ValueError as exc:
            if str(exc).startswith(("Quelle meldet HTTP 404;", "Quelle meldet HTTP 410;")):
                raise ProductDetailFailure("Amazon-Produktseite nicht mehr verfügbar") from exc
            raise
        try:
            offer = self._parse_offer(page, url, prime=self._prime)
        except ValueError as exc:
            raise ProductDetailFailure(str(exc)) from exc
        return self._store(self._offers, key, offer)

    def _parse_offer(self, html: str, url: str, *, prime: bool = False) -> Offer:
        asin = url.rsplit("/", 1)[-1]
        root = _DOM(html).root
        canonical = [node for node in root.nodes() if node.tag == "link" and node.attrs.get("rel") == "canonical"]
        if self._product_url(_only(canonical, "kanonischen Produktlink").attrs.get("href")) != url:
            raise ValueError("Amazon-Produktlink widerspricht der angefragten ASIN")
        product = _only(root.by_id("ppd"), "Produktbereich")
        title = _only(product.by_id("title_feature_div"), "Produkttitel")
        if title.attrs.get("data-csa-c-asin") != asin:
            raise ValueError("Amazon-Produkttitel gehört nicht zur angefragten ASIN")
        box, merchant_id = _buybox(product, asin)
        seller = _seller(box, asin, merchant_id)
        apex = _only(product.by_id("apex_desktop"), "Preisbereich")
        price_cents = _price(apex, asin)
        shipping_cents = _shipping(box, price_cents)
        price_basis = "regular"
        if prime:
            price_rows = apex.by_id("apex_desktop_primeSavingsUpsellAccordionRow")
            seller_rows = product.by_id("primeSavingsUpsellAccordionRow")
            if len(price_rows) == len(seller_rows) == 1:
                try:
                    if _seller(seller_rows[0], asin, merchant_id) == seller:
                        price_cents = _price(price_rows[0], asin, prime=True)
                        shipping_cents = _shipping(seller_rows[0], price_cents)
                        price_basis = "prime"
                except ValueError:
                    # An unverified member-only row does not displace the ordinary price.
                    pass
        facts = _details(root, asin)
        gtins = {canonical_gtin(facts.get(key, "")): facts.get(key, "") for key in ("ean", "gtin", "upc")}
        gtins.pop(None, None)
        if len(gtins) > 1:
            raise ValueError("Amazon liefert widersprüchliche Produkt-Barcodes")
        new = any((node.attrs.get("data-a-accordion-row-name") or "") == "newAccordionRow" for node in box.ancestors())
        return Offer(
            source=self.id, merchant="Amazon.de", product_id=asin, url=url,
            name=_only(title.by_id("productTitle"), "Produkttitel").text()[:300],
            seller=seller, price_cents=price_cents, price_basis=price_basis,
            shipping_cents=shipping_cents, availability=_availability(box, asin),
            condition="new" if new else "unknown", variant=_variant(product, asin),
            gtin=next(iter(gtins.values()), ""), model=facts.get("modellnummer", ""),
            manufacturer=facts.get("marke") or facts.get("hersteller", ""), checked_at=time(),
        )

    async def _links(self, term: str) -> list[tuple[str, str]]:
        expires, links = self._searches.get(term, (0, None))
        if monotonic() < expires:
            return links
        query = urlencode({self.search_parameter: term})
        root = _DOM(await self._page(f"{self.origin}{self.search_path}?{query}")).root
        links = {}
        for card in root.nodes():
            if (card.attrs.get("role") != "listitem" or card.attrs.get("data-component-type") != "s-search-result"
                    or card.has_class("AdHolder")):
                continue
            try:
                asin = _asin(card.attrs.get("data-asin"))
            except ValueError:
                continue
            headings = [node for node in card.nodes() if node.tag == "h2"]
            if len(headings) == 1 and (title := headings[0].text()):
                links.setdefault(f"{self.origin}/dp/{asin}", title)
            if len(links) >= 30:
                break
        return self._store(self._searches, term, list(links.items()))

    async def search(self, watch) -> list[Offer]:
        if watch.kind != "discovery" or not watch.query:
            raise ValueError("Amazon-Suche erfordert einen Suchbegriff")
        async with self._lock:
            self._prime = watch.amazon_prime
            return await self._find([watch.query, *watch.synonyms])

    async def lookup(self, watch) -> list[Offer]:
        if watch.kind != "exact":
            raise ValueError("Amazon-Abfrage erfordert eine exakte Produktüberwachung")
        if not (watch.url or watch.product_id):
            raise ValueError("Für die exakte Amazon-Überwachung bitte Produktlink oder ASIN angeben")
        url = self._product_url(watch.url) if watch.url else f"{self.origin}/dp/{_asin(watch.product_id)}"
        if watch.product_id and _asin(watch.product_id) != url.rsplit("/", 1)[-1]:
            raise ValueError("Amazon-Link und ASIN widersprechen sich")
        async with self._lock:
            self._prime = watch.amazon_prime
            offer = await self._offer_at(url)
        if (watch.gtin and (not offer.gtin or canonical_gtin(watch.gtin) != canonical_gtin(offer.gtin))
                or any(getattr(watch, field) and getattr(watch, field).casefold() != getattr(offer, field).casefold()
                       for field in ("model", "variant"))):
            raise ValueError("Amazon-Produktidentität widerspricht der Überwachung")
        return [offer]
