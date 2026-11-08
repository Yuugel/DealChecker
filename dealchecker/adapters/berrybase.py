"""Public BerryBase Germany product pages and shop search."""
import re
from html import unescape

from .public_pages import PublicShopAdapter, _text


_EAN = re.compile(
    r'<th\s+class="properties-label"[^>]*>\s*EAN:\s*</th>\s*'
    r'<td\s+class="properties-value"[^>]*>\s*<span[^>]*>\s*([0-9]{8,14})\s*</span>',
    re.IGNORECASE,
)
_COLOR = re.compile(r'<li[^>]*>\s*Farbe:\s*([^<>]+)</li>', re.IGNORECASE)


class BerryBaseAdapter(PublicShopAdapter):
    id = "berrybase"
    name = "BerryBase"
    description = "Öffentliche BerryBase-Produktsuche und deutsche Direktlinks"
    url_hosts = ("www.berrybase.de",)
    product_path = re.compile(r"/[a-z0-9]+(?:-[a-z0-9]+)+")
    search_path = "/search"
    search_parameter = "search"
    listing_link_class = "product-name"
    seller_name = "BerryBase"

    def _page_fields(self, html, product, url):
        eans = set(_EAN.findall(html))
        if len(eans) > 1:
            raise ValueError("BerryBase-Produktseite enthält mehrere EAN")
        color = _text(product.get("color"))
        if not color:
            title = _text(product.get("name")).casefold()
            colors = {_text(unescape(value)) for value in _COLOR.findall(html)}
            matched = {value for value in colors if title.endswith(", " + value.casefold())}
            if len(matched) == 1:
                color = matched.pop()
        return _text(product.get("sku")), color, next(iter(eans), "")
