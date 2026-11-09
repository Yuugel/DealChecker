"""Discord webhook formatting over the injected guarded HTTP client."""
from urllib.parse import urlsplit
from datetime import datetime, timezone
import re

from .network import validate_url


class DiscordNotifier:
    def __init__(self, client, webhook_url: str):
        validate_url(webhook_url, {'discord.com', 'discordapp.com'})
        parts = urlsplit(webhook_url)
        if (parts.scheme != "https" or parts.hostname not in {"discord.com", "discordapp.com"}
                or parts.port is not None or parts.username or parts.password or parts.query or parts.fragment
                or not re.fullmatch(r'/api/webhooks/[0-9]{1,25}/[A-Za-z0-9_-]+', parts.path)):
            raise ValueError("invalid Discord webhook URL")
        self.client = client
        self.webhook_url = webhook_url

    async def send(self, event: dict) -> None:
        amount = event.get("alert_cents", event.get("total_cents"))
        price = f"{amount // 100},{amount % 100:02d} €"
        item_only = event.get("price_scope") == "item"
        price_label = "Artikelpreis (Versandkosten unbekannt; Gesamtpreis offen)" if item_only else "Gesamtpreis"
        lines = [f"{event['reason']}: {event.get('product', event.get('name', 'Produkt'))} — {price} {price_label} bei {event['merchant']}"]
        if event.get('price_basis') == 'prime':
            lines.append('Prime-Preis · Amazon-Prime-Mitgliedschaft erforderlich')
        if event.get("comparison_cents") is not None:
            cents = event["comparison_cents"]
            label = "Bisher beobachteter Artikelpreis" if item_only else "Bisher beobachteter Gesamtpreis"
            lines.append(f"{label}: {cents // 100},{cents % 100:02d} €")
        if event.get("savings_cents") is not None:
            cents = event["savings_cents"]
            lines.append(f"Preisunterschied zur eigenen {('Artikelpreis-' if item_only else 'Gesamtpreis-')}Historie: {cents // 100},{cents % 100:02d} €")
        observed = datetime.fromtimestamp(event["checked_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        availability = event["availability"]
        if availability == "in_stock":
            status = "Laut Feed verfügbar" if event.get("provenance") == "feed" else "Verfügbar (laut Quelle)"
        elif availability == "out_of_stock":
            status = "Laut Feed nicht verfügbar" if event.get("provenance") == "feed" else "Nicht verfügbar (laut Quelle)"
        else:
            status = "Verfügbarkeit unbekannt"
        lines.append(f"Beobachtet: {observed} · {status}")
        lines.append(event["url"])
        content = "\n".join(lines)
        await self.client.post_json(self.webhook_url,
                                    {"content": content[:2000], "allowed_mentions": {"parse": []}},
                                    allowed_hosts={"discord.com", "discordapp.com"})
