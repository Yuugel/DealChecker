# Modula24: Möbelteile aus einer autorisierten Händlerquelle

Modula24 veröffentlicht [eine eigene API-Dokumentation](https://modula24.com/pages/api) für Händler, Preisvergleiche, Entwickler und KI-Agenten. Sie beschreibt den [deutschen JSON-Produktfeed](https://modula24.com/feeds/products.de.json) und die Bestands-API ausdrücklich als öffentlich, kostenlos, lesend und ohne Anmeldung nutzbar. Die [FAQ](https://modula24.com/pages/faq) bestätigt diesen Zugangsweg. Der Adapter liest ausschließlich den festen Feed-Endpunkt über den gemeinsamen HTTPS-Client; er crawlt keine Produktseiten und verwendet keine Zugangsdaten.

## Tatsächliche Abdeckung

Der Feed enthält bestellbare Möbelteile, Zubehör und Werkzeuge **von Modula24** in deutscher Sprache. Es ist keine allgemeine Suche nach Haushaltswaren, Elektronik oder Lebensmitteln und kein Händlervergleich. Neue Treffer werden durch freie Suche über Titel, Hersteller, Kategorie und Variantenangaben im endlichen Katalog entdeckt. Exakte Überwachung verwendet die `variant_id` aus dem Feed. Die Angebots-URL erhält `?variant=ID`, damit Varianten desselben Produkts nicht vermischt werden. Die separate Bestands-API verwendet andere SKU-Kennungen; diese werden nicht mit Feed-Varianten-IDs gleichgesetzt.

Der Adapter übernimmt EUR-Preise, Verfügbarkeit und den im Feed angegebenen Versandpreis für **ein Stück nach Deutschland**. Fehlt ein DE-Versandpreis, bleibt Versand unbekannt. Mengenrabatte, Kombinationen im Warenkorb und der endgültige Kassenpreis können abweichen. Ein Angebot ist eine Momentaufnahme; vor dem Kauf zählt der Shoppreis.

## Abruf und Grenzen

Der gesamte Feed wird höchstens alle fünf Minuten neu abgerufen. Gleichzeitige Prüfungen teilen sich einen Abruf; Cachetreffer behalten dessen ursprünglichen Prüfzeitpunkt. `generated` bezeichnet die letzte Feed-Erzeugung nach einer Shopänderung und wird nicht als Zeitpunkt eines aktuellen Abrufs ausgegeben. Fehlerhafte IDs, Preise, Währungen, Varianten-URLs oder doppelte IDs führen zu einem sichtbaren Fehler, statt fragliche Preise zu melden. Höchstens 5.000 Varianten werden verarbeitet.

Die Antwort maß am **8. November 2026** 2.078.537 UTF-8-Bytes und liegt damit knapp unter dem allgemeinen 2-MiB-Limit. Nur dieser feste Feed-Abruf erhält ein explizites 4-MiB-Limit; für andere Quellen bleibt 2 MiB. Wächst der Feed darüber hinaus, schlägt der Abruf sichtbar fehl. Die [Modula24-Dokumentation](https://modula24.com/pages/api) empfiehlt, Ergebnisse nicht häufiger als nötig abzurufen und kurz zwischenzuspeichern.

## Begrenzter Live-Nachweis

Am **8. November 2026, 14:12 UTC**, lieferte der dokumentierte Feed 698 Varianten, `currency=EUR` und `generated=2026-11-08T12:34:48.496Z`. Der Adapter las daraus den Konnektor mit `variant_id=58436517069064`, 1,50 € Artikelpreis, 5,22 € DE-Versand für ein Stück und `in_stock`. Der [Produkt-JSON-Endpunkt](https://modula24.com/products/konnektor-connector-verbindung-usm-haller.json) bestätigte dieselbe Variante mit 1,50 € und `available=true`; die [Produktseite](https://modula24.com/products/konnektor-connector-verbindung-usm-haller) zeigte 1,50 €. Dies ist keine beobachtete Preisänderung und keine Bestätigung eines späteren Kaufabschlusses. Die automatisierten Tests verwenden ausschließlich synthetische Fixtures.
