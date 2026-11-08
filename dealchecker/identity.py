"""Conservative identity for contemporaneous delivered-price comparisons."""
import re


def canonical_gtin(value: str) -> str | None:
    digits = re.sub(r"[ -]", "", value)
    if not digits.isascii() or not digits.isdigit() or len(digits) not in {8, 12, 13, 14}:
        return None
    weighted = sum(int(digit) * (3 if index % 2 == 0 else 1)
                   for index, digit in enumerate(reversed(digits[:-1])))
    if (weighted + int(digits[-1])) % 10:
        return None
    return digits.zfill(14)


def matches_product_term(offer, term: str) -> bool:
    """Discovery relevance only; exact identity is always checked separately."""
    gtin = canonical_gtin(term)
    if gtin:
        return canonical_gtin(offer.gtin) == gtin
    tokens = set(re.findall(r'\w+', term.casefold()))
    text = ' '.join((offer.name, offer.model, offer.manufacturer, offer.category, offer.variant))
    return bool(tokens) and tokens.issubset(set(re.findall(r'\w+', text.casefold())))


def comparison_identity(offer: dict) -> tuple | None:
    gtin = offer.get("gtin", "")
    manufacturer = offer.get("manufacturer", "").strip().casefold()
    model = offer.get("model", "").strip().casefold()
    variant = offer.get("variant", "").strip().casefold()
    pack = offer.get("pack_size", "").strip().casefold()
    condition = offer.get("condition", "unknown")
    if gtin:
        canonical = canonical_gtin(gtin)
        if canonical is None:
            return None
        return ("gtin", canonical, variant, pack, condition, manufacturer, model)
    if manufacturer and model and variant and pack:
        return ("manufacturer_model", f"{manufacturer} / {model}", variant, pack, condition)
    return None
