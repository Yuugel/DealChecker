"""Public ALTERNATE Germany product pages and shop search."""
import re
from html import unescape

from .public_pages import PublicShopAdapter, _text


_VARIANT = re.compile(r'<small\s+class="[^"]*\bproduct-name-sub\b[^"]*"[^>]*>\s*\(([^<>]+)\)\s*</small>')


class AlternateAdapter(PublicShopAdapter):
    id = "alternate"
    name = "ALTERNATE"
    description = "Öffentliche ALTERNATE-Produktsuche und deutsche Direktlinks"
    url_hosts = ("www.alternate.de",)
    product_path = re.compile(r"/[^/?#%\\\s]+/[^/?#%\\\s]+/html/product/[0-9]{1,15}")
    search_path = "/listing.xhtml"
    search_parameter = "q"
    listing_link_class = "productBox"
    seller_name = "ALTERNATE GmbH"

    def _page_fields(self, html, product, url):
        sku = url.rsplit("/", 1)[-1]
        if str(product.get("sku")) != sku:
            raise ValueError("ALTERNATE-Artikelnummer widerspricht dem Produktlink")
        variant = _VARIANT.search(html)
        return sku, _text(unescape(variant[1])) if variant else "", ""
