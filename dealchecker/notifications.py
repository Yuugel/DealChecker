"""Discord webhook formatting over the injected guarded HTTP client."""
from urllib.parse import urlsplit
from datetime import datetime, timezone


class DiscordNotifier:
    def __init__(self, client, webhook_url: str):
        parts = urlsplit(webhook_url)
        if (parts.scheme != "https" or parts.hostname not in {"discord.com", "discordapp.com"}
                or parts.port is not None or parts.username or parts.password or parts.query or parts.fragment
                or not parts.path.startswith("/api/webhooks/") or len(parts.path.split("/")) != 5
                or any(not segment for segment in parts.path.split("/")[3:])):
            raise ValueError("invalid Discord webhook URL")
        self.client = client
        self.webhook_url = webhook_url

    async def send(self, event: dict) -> None:
        total = event["total_cents"]
        price = f"{total // 100},{total % 100:02d} €"
        lines = [f"{event['reason']}: {event.get('product', event.get('name', 'Produkt'))} — {price} bei {event['merchant']}"]
        if event.get("comparison_cents") is not None:
            cents = event["comparison_cents"]
            lines.append(f"Bisher beobachteter Vergleichspreis: {cents // 100},{cents % 100:02d} €")
        if event.get("savings_cents") is not None:
            cents = event["savings_cents"]
            lines.append(f"Preisunterschied zur eigenen Historie: {cents // 100},{cents % 100:02d} €")
        observed = datetime.fromtimestamp(event["checked_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"Beobachtet: {observed} · Verfügbarkeit: {event['availability']}")
        lines.append(event["url"])
        content = "\n".join(lines)
        await self.client.post_json(self.webhook_url,
                                    {"content": content[:2000], "allowed_mentions": {"parse": []}},
                                    allowed_hosts={"discord.com", "discordapp.com"})
