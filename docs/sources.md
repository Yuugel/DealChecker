# Datenquellen (Stand: 8. November 2026)

## Sofort nutzbar: Modula24, physische Möbelteile

Modula24 erlaubt in seiner [eigenen API-Dokumentation](https://modula24.com/pages/api) kostenlose, lesende Zugriffe ohne Anmeldung und nennt Preisvergleiche als Anwendungsfall. Der Standardadapter durchsucht den [deutschen JSON-Produktkatalog](https://modula24.com/feeds/products.de.json) und prüft ausgewählte Varianten gezielt über die dokumentierte Produkt-JSON-Schnittstelle samt lesender DE-Versandabfrage. Nur bei einer reinen, noch nicht aufgelösten Varianten-ID wird einmal der Katalog benötigt. Es werden keine Shopseiten ausgelesen. Ein Zugangsschlüssel, eine kostenpflichtige API oder eine Partnerschaft sind für diesen dokumentierten Zugang nicht erforderlich.

Die Abdeckung umfasst **ausschließlich Möbelteile und Zubehör dieses Händlers**, etwa Kugeln, Konnektoren, Rohre und Tablare. Sie erfüllt einen realen Such- und Überwachungspfad für physische Produkte, deckt aber nicht den allgemeinen deutschen Haushalt-, Elektronik-, Küchen- oder Lebensmittelmarkt ab. Preise und Verfügbarkeit sind Händlerangaben; Versand bezieht sich auf ein Stück nach Deutschland, keine Warenkorb- oder Mengenberechnung. Fehlt dieser Versandpreis, bleibt er unbekannt und verhindert einen Gesamtpreisalarm.

Fünf Minuten Cache, feste Händler-URL und begrenzte Kataloggröße halten den Zugriff kontrolliert. Die einzelnen Angebotslinks und stabilen Varianten-IDs bleiben erhalten. [Technische Grenzen, Datenzeitpunkte und Live-Nachweis](modula24-source.md).

## Sofort nutzbar: Bestenvergleich, breite Preisreferenzen

Der neue Standardadapter nutzt die [freigegebene öffentliche API](https://bestenvergleich.de/entwickler) für Produktsuche und gezielte Produkt-/EAN-Abfragen. Live gefunden wurden Airfryer sowie Bosch-Backofen-/Kochfeldkombinationen. Die API liefert Artikel-Mindestpreise, aber in den geprüften Antworten keine einzelnen Händlerangebote. Versand, Verkäufer und Bestand bleiben daher unbekannt; keine Kaufpreisalarme und kein verifizierter Gesamtpreisvergleich. [Technische Beschreibung und Live-Proben](bestenvergleich-source.md).

## Sofort nutzbar: Apple App Store Deutschland

Der Standardadapter nutzt Apples [dokumentierte iTunes Search API](https://performance-partners.apple.com/search-api), `country=de`, `media=software` und `entity=software`. Eine freie Suche entdeckt neue Apps und Spiele; eine spätere `lookup`-Abfrage mit der numerischen `trackId` prüft das ausgewählte Produkt erneut. Ein Zugangsschlüssel ist für diese lesenden Aufrufe nicht erforderlich. Am 8. November 2026 lieferten Suche und Lookup für „Monument Valley“ im deutschen Store `trackId=728293409`, `price=3.99`, `currency=EUR`.

Apple nennt ungefähr 20 Abfragen pro Minute und empfiehlt Caching. Der gemeinsame HTTP-Client hält mindestens 3,2 Sekunden Abstand zu diesem Host. Gleiche Such- und Lookup-Anfragen werden 300 Sekunden im Speicher vorgehalten (höchstens 128 Einträge), wobei der ursprüngliche Prüfzeitpunkt erhalten bleibt. Ein Suchauftrag fragt höchstens sechs Begriffe (Hauptbegriff und Synonyme) nacheinander ab; weitere unterschiedliche Begriffe verursachen einen sichtbaren Fehler. Die Suchantworten sind auf 50 Treffer je Begriff begrenzt; sehr breite Suchen sind daher möglicherweise unvollständig.

Abgedeckt sind **App-Kaufpreise**, einschließlich kostenloser Apps (0 €). Apple-Arcade-Einträge ohne Einzelkaufpreis werden ausgelassen. In-App-Käufe, Abos, physische Waren, lokale Verfügbarkeit und Preise anderer Händler fehlen. Für digitale App-Käufe sind Versandkosten 0 €. Die Existenz des kaufbaren App-Eintrags gilt als `in_stock`; der Adapter bestätigt keine installierbare Gerätekompatibilität. Preise sind Momentaufnahmen. Der Adapter verwendet Text und den Store-Link; App-Icons oder Vorschauen werden nicht übernommen, weil Apple für [Werbeinhalte zusätzliche Platzierungsbedingungen](https://performance-partners.apple.com/search-api) nennt.

Eine exakte Überwachung sollte die `trackId` oder eine deutsche `https://apps.apple.com/de/.../idNUMBER`-URL verwenden. Nur wenn genau ein Treffer denselben Titel ohne Beachtung der Großschreibung hat, wird ein titelbasierter Suchauftrag zugeordnet. GTIN, Modellnummer und Variante lassen sich mit dieser Quelle nicht bestätigen und werden sichtbar abgelehnt.

## Optionale autorisierte öffentliche JSON-Feeds

Betreiber dürfen einen **bereits autorisierten, öffentlich abrufbaren** HTTPS-Produktfeed beim lokalen Start über die Umgebungs-Konfiguration eintragen. Es gibt keine Oberfläche zum freien Crawlen beliebiger URLs. Feed-URLs mit Zugangsdaten oder Query-Token sind nicht unterstützt. Eine Authentifizierung für private Feeds ist noch nicht implementiert. Der HTTP-Client prüft öffentliche DNS-Ziele, verbietet Weiterleitungen, begrenzt Antworten auf 2 MiB und setzt Timeouts; der Adapter liest einen ganzen endlichen Katalog höchstens alle fünf Minuten neu.

JSON-Schema: Wurzelobjekt mit genau `offers` als Liste. Jede Zeile benötigt `product_id`, `name`, `url`, `price_cents`, `shipping_cents` (ganze nichtnegative Cent oder `null`), `currency` (`EUR`), `availability` (`in_stock`, `out_of_stock`, `unknown`) und `merchant`. Optional sind `variant`, `gtin`, `model`, `manufacturer`, `category`, `coupon`, `unit_price`, `seller`, `pack_size`, `condition` (`new`, `used`, `refurbished`, `unknown`), `offer_type` (`retail`, `reference`) und `observed_at` (UTC-Epoch-Sekunden). Fehlt `observed_at`, gilt der Abrufzeitpunkt als Katalogbeobachtung; er bestätigt keinen live verfügbaren Lagerbestand. Liegt `observed_at` mehr als zehn Minuten zurück, erhält der Treffer `quality=feed_stale` und darf keinen Alarm auslösen. Weitere Pflichtfelder oder andere Währungen werden nicht stillschweigend umgedeutet. Angebotslinks müssen sichere öffentliche HTTPS-URLs sein.

Das folgende Beispiel ist **synthetisch** und stellt kein echtes Händlerangebot dar:

```json
{
  "offers": [
    {
      "product_id": "BEISPIEL-1",
      "name": "Beispiel-Controller Blau",
      "url": "https://shop.example/products/BEISPIEL-1",
      "price_cents": 2999,
      "shipping_cents": 499,
      "currency": "EUR",
      "availability": "unknown",
      "merchant": "Beispielhändler",
      "variant": "Blau",
      "gtin": "1234567890123",
      "model": "BEISPIEL-1"
    }
  ]
}
```

## Weitere geprüfte Quellen

Die [Phase-2-Matrix mit 20 Händlern/Plattformen](phase2-sources.md) dokumentiert aktuelle technische Proben, Nutzungsbedingungen und Zugriffshürden. [Konkrete Freischaltungsschritte](partner-access.md) priorisieren zugängliche Publisherfeeds. Die folgende Übersicht enthält zusätzlich historische MVP-Kandidaten.

| Quelle | Offizieller Zugangsweg | MVP-Status |
|---|---|---|
| eBay | [Browse API](https://developer.ebay.com/develop/api/buy/browse_api) mit Application Access Token und [Produktionsfreigabe](https://developer.ebay.com/api-docs/buy/buy-requirements.html) | Noch nicht integriert. Entwicklerkonto/Schlüssel, Partnerantrag und Produktionszulassung sind nötig; Freigabe nicht garantiert. |
| Amazon | [Creators API](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction) mit angenommenem Associates-Konto, qualifizierten Verkäufen und API-Zugangsdaten | Nicht frei ohne Voraussetzungen; [Richtlinien](https://affiliate-program.amazon.com/help/operating/policies) begrenzen Datennutzung. |
| MediaMarkt und Saturn | [MediaMarkt-Partnerprogramm](https://www.mediamarkt.de/de/about-us/affiliate/partnere), [Saturn-Partnerseite](https://www.saturn.de/de/legal/affiliates/) | Partnerzugang prüfen; kein unautorisierter Website-Scraper. |
| OTTO | [Affiliate-Produktdaten](https://www.otto.de/partnerprogramm/wissenswertes/bewerbungsrichtlinien/) | Feed nach bestätigter Partnerschaft; OTTO Market API dient Händlern für eigene Produkte. |
| idealo | [Affiliate-Programm mit API-Option](https://partner.idealo.com/de/affiliate-marketing-programm) | Nur nach Partnerfreigabe; [PWS 2.0](https://idealo.github.io/partner-web-service/docs/v2/) dient Händlerbeständen. |
| Keepa | [API-Abonnement](https://keepa.com/api-docs/plans-tokens.html) und Schlüssel | Wegen laufender Kosten nicht aktiviert. |
| Geizhals, mydealz | Keine frei nutzbare offizielle Preis-API verifiziert | Keine Integration; eine Freigabe oder ein autorisierter Feed wäre vorab nötig. |
| Open Prices | [Offene API](https://openfoodfacts.github.io/openfoodfacts-server/api/tutorials/product-prices/) | Historische, von Nutzern belegte Lebensmittelpreise; keine aktuellen Händlerangebote. [ODbL-Bedingungen](https://openfoodfacts.github.io/open-prices/guides/data/) sind bei Wiederverwendung zu beachten. |
| Steam | [Offizielle Web-API](https://partner.steamgames.com/doc/webapi_overview) | `storesearch`/`appdetails` auf dem Store-Host sind dort nicht dokumentiert; nicht als erlaubte Produkt-API eingebaut. |

Fehlende Händler erscheinen nicht als überwachte oder geprüfte Quellen. Zusätzliche Feed-Konfigurationen stellen nur die Daten des jeweiligen autorisierten Feed-Betreibers bereit und bedeuten keine allgemeine Marktabdeckung.

Für breitere physische Abdeckung ist der nächste konkrete Weg ein genehmigter eBay-Browse-Zugang: Entwicklerkonto und Anwendungsschlüssel einrichten, den Buy-API-Produktionszugang über das Partnerverfahren beantragen, Nutzungsbedingungen und mögliche Kosten bestätigen und anschließend einen Adapter mit sicherer Token-Konfiguration sowie Live-Such-/Preisprüfungen ergänzen. Alternativ kann ein Händler seinen Produktfeed ausdrücklich freigeben. Es wurde kein kostenpflichtiger Vertrag abgeschlossen und kein Zugang beantragt. Allgemein erreichbare Shopify-Endpunkte sind keine pauschale Freigabe: Die [Ajax-API-Dokumentation](https://shopify.dev/docs/api/ajax) beschränkt deren Nutzung auf Shopify-gehostete Themes; Modula24 erteilt dagegen für seine eigenen dokumentierten Schnittstellen ausdrücklich Zugang.
