"""Synthetic fixtures modeled on Amazon's public German product markup."""
from html import escape

import pytest

from dealchecker.adapters.amazon import AmazonAdapter
from dealchecker.models import WatchInput
from dealchecker.service import _evaluate

ORIGIN = "https://www.amazon.de"
ASIN = "B0A1234567"
OTHER = "B0A1234568"
URL = ORIGIN + "/dp/" + ASIN


class Pages:
    def __init__(self, pages, robots="User-agent: *\nDisallow: /checkout/"):
        self.pages = {ORIGIN + "/robots.txt": robots, **pages}
        self.calls = []

    async def get_text(self, url, **kwargs):
        self.calls.append(url)
        return self.pages[url]


def price(asin, euro, *, member=False):
    return (
        f'<div id="corePriceDisplay_desktop_feature_div" data-csa-c-asin="{asin}">'
        f'<span class="a-price priceToPay{" prime-exclusive" if member else ""}">'
        f'<span class="a-price-whole">{euro},</span><span class="a-price-fraction">42</span>'
        '<span class="a-price-symbol">€</span></span></div>'
    )


def page(asin=ASIN, *, buybox_asin=None, prices=None, condition="für qualifizierte Erstbestellung",
         benefit="ncp", subtype="CONDITIONALLY_FREE", seller_id="AEXAMPLE01"):
    return f"""
    <link rel="canonical" href="{ORIGIN}/dp/{asin}">
    <div id="ppd">
      <div id="title_feature_div" data-csa-c-asin="{asin}">
        <h1><span id="productTitle">Example Gamepad 2C (Mint)</span></h1>
      </div>
      <div id="desktop_buybox">
        <form id="addToCart" action="/gp/product/handle-buy-box/ref=dp_start">
          <input type="hidden" name="ASIN" value="{buybox_asin or asin}">
          <input type="hidden" name="merchantID" value="AEXAMPLE01">
          <input type="hidden" name="isMerchantExclusive" value="0">
          <div id="desktop_qualifiedBuyBox" data-csa-c-asin="{asin}">
            <div id="availabilityInsideBuyBox_feature_div" data-csa-c-asin="{asin}">
              <span class="primary-availability-message">Nur noch 8 auf Lager</span>
            </div>
            <div id="merchantInfoFeature_feature_div" data-csa-c-asin="{asin}">
              <a id="sellerProfileTriggerId" href="/seller?seller={seller_id}&amp;asin={asin}">Example Seller</a>
            </div>
            <div id="deliveryBlockMessage">
              <div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">
                <span data-csa-c-delivery-price="GRATIS" data-csa-c-delivery-condition="{escape(condition)}"
                      data-csa-c-delivery-benefit-program-id="{benefit}" data-csa-c-mir-type="DELIVERY"
                      data-csa-c-mir-sub-type="{subtype}" data-csa-c-mir-variant="DEFAULT">GRATIS Lieferung Dienstag {escape(condition)}</span>
              </div>
            </div>
            <input id="add-to-cart-button" type="submit">
          </div>
        </form>
      </div>
      <div id="apex_desktop" data-csa-c-asin="{asin}">{prices if prices is not None else price(asin, 32)}</div>
      <div id="inline-twister-row-color_name">
        <span id="inline-twister-expanded-dimension-text-color_name">Mint</span>
        <li data-initiallySelected="true" data-asin="{asin}">Mint</li>
      </div>
      <div id="detailBullets_feature_div" data-csa-c-asin="{asin}">
        <li><span class="a-list-item"><span>Modellnummer:</span> <span>EX-200</span></span></li>
        <li><span class="a-list-item"><span>Hersteller:</span> <span>Example</span></span></li>
      </div>
      <div class="coupon">Spare 10,00 € mit Coupon</div>
    </div>
    """


def exact(**kwargs):
    return WatchInput(name="Example Gamepad", source_ids=["amazon"], **kwargs)


@pytest.mark.asyncio
async def test_direct_amazon_keeps_buybox_identity_and_does_not_apply_coupon_or_first_order_shipping():
    transport = Pages({URL: page()})
    adapter = AmazonAdapter(transport)
    watch = exact(url=URL + "?tag=not-forwarded", variant="Mint", model="EX-200")
    offer, = await adapter.lookup(watch)
    assert (offer.url, offer.price_cents, offer.seller, offer.availability) == (
        URL, 3242, "Example Seller", "in_stock")
    assert (offer.variant, offer.model, offer.manufacturer, offer.shipping_cents, offer.gtin) == (
        "Mint", "EX-200", "Example", None, "")
    alert_watch = exact(url=URL, target_cents=4000)
    assert _evaluate(alert_watch, offer, [], None, 1800, offer.checked_at + 1)[-1] is None
    assert (await adapter.lookup(exact(product_id=ASIN)))[0].checked_at == offer.checked_at
    assert transport.calls == [ORIGIN + "/robots.txt", URL]


@pytest.mark.asyncio
async def test_amazon_uses_regular_new_price_instead_of_prime_accordion():
    html = page(prices=(
        f'<div id="apex_desktop_primeSavingsUpsellAccordionRow">{price(ASIN, 24, member=True)}</div>'
        f'<div id="apex_desktop_newAccordionRow">{price(ASIN, 55)}</div>'
    ))
    offer, = await AmazonAdapter(Pages({URL: html})).lookup(exact(url=URL))
    assert offer.price_cents == 5542


@pytest.mark.asyncio
@pytest.mark.parametrize("html", [
    page(buybox_asin=OTHER),
    page(prices=price(ASIN, 32) + price(ASIN, 23)),
    page(seller_id="ANOTHERSELLER"),
    page(prices=price(ASIN, 24, member=True)),
    page().replace(f'data-asin="{ASIN}"', f'data-asin="{OTHER}"'),
])
async def test_amazon_rejects_wrong_or_ambiguous_buybox(html):
    with pytest.raises(ValueError):
        await AmazonAdapter(Pages({URL: html})).lookup(exact(url=URL))


@pytest.mark.asyncio
async def test_amazon_search_enriches_exact_cards_and_skips_invalid_product_pages():
    cards = (
        f'<div role="listitem" data-asin="{OTHER}" data-component-type="s-search-result"><h2>Example Gamepad</h2></div>'
        f'<div role="listitem" data-asin="{ASIN}" data-component-type="s-search-result"><h2>Example Gamepad</h2></div>'
    )
    transport = Pages({ORIGIN + "/s?k=Example+Gamepad": cards, ORIGIN + "/dp/" + OTHER: "<h1>Missing</h1>", URL: page()})
    watch = WatchInput(name="Example Gamepad", kind="discovery", query="Example Gamepad", source_ids=["amazon"])
    offers = await AmazonAdapter(transport).search(watch)
    assert [offer.product_id for offer in offers] == [ASIN]
    assert offers.skipped_pages == 1


@pytest.mark.asyncio
async def test_amazon_robot_check_stops_host_reads():
    transport = Pages({URL: '<title>Robot Check</title><form action="/errors/validateCaptcha"></form>'})
    adapter = AmazonAdapter(transport)
    for _ in range(2):
        with pytest.raises(ValueError, match="Zugriff"):
            await adapter.lookup(exact(url=URL))
    assert transport.calls == [ORIGIN + "/robots.txt", URL]


def prime_page(*, seller_id="AEXAMPLE01"):
    html = page(prices=(
        f'<div id="apex_desktop_primeSavingsUpsellAccordionRow">{price(ASIN, 24, member=True)}</div>'
        f'<div id="apex_desktop_newAccordionRow">{price(ASIN, 55)}</div>'
    ))
    prime_buybox = (
        '<div id="primeSavingsUpsellAccordionRow">'
        f'<div id="merchantInfoFeature_feature_div" data-csa-c-asin="{ASIN}">'
        f'<a id="sellerProfileTriggerId" href="/seller?seller={seller_id}&amp;asin={ASIN}">Example Seller</a>'
        '</div></div>'
    )
    return html.replace('<div id="desktop_buybox">', '<div id="desktop_buybox">' + prime_buybox)


@pytest.mark.asyncio
async def test_amazon_prime_requires_opt_in_and_separate_observations():
    transport = Pages({URL: prime_page()})
    adapter = AmazonAdapter(transport)
    regular_watch = exact(url=URL)
    prime_watch = exact(url=URL, amazon_prime=True)
    regular, = await adapter.lookup(regular_watch)
    prime, = await adapter.lookup(prime_watch)
    regular_again, = await adapter.lookup(regular_watch)
    assert (regular.price_cents, prime.price_cents, regular_again.price_cents) == (5542, 2442, 5542)
    assert regular.price_basis == "regular"
    assert prime.price_basis == "prime"
    assert prime.shipping_cents is None
    assert regular_again.checked_at == regular.checked_at


@pytest.mark.asyncio
async def test_amazon_prime_without_matching_seller_uses_regular_price():
    adapter = AmazonAdapter(Pages({URL: prime_page(seller_id="ANOTHERSELLER")}))
    watch = exact(url=URL, amazon_prime=True)
    offer, = await adapter.lookup(watch)
    assert offer.price_cents == 5542
    assert offer.price_basis == "regular"


@pytest.mark.asyncio
async def test_amazon_confirms_unconditional_free_shipping_from_primary_delivery():
    html = page(prices=price(ASIN, 55), condition="", benefit="cfs", subtype="")
    offer, = await AmazonAdapter(Pages({URL: html})).lookup(exact(url=URL))
    assert offer.shipping_cents == 0
    assert offer.total_cents == 5542
