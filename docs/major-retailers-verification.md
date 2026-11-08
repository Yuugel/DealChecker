# Große Händler: Prüfstand 8. November 2026

Basis: gemergte PR #3, `main` bei `7919112`. Neuer Branch `feature/major-retailer-data`.
Nach Nutzerentscheidung bleiben externe API-Konten unkonfiguriert. Kein Konto, Kauf,
Abo, Autostart oder produktiver Benachrichtigungskanal wurde eingerichtet.

Der am 9. November ergänzte **Amazon-Direktzugang mit Prime-Auswahl** ist separat [live geprüft und dokumentiert](amazon-direct.md). Die folgenden Nachweise vom 8. November bleiben als bisheriger Stand erhalten.

## Ohne API-Konto: echte Ende-zu-Ende-Prüfung

`python scripts/verify_major_sources.py --output artifacts/major-live-20261108-a`
benutzte den regulären HTTPS-Client, die echten Adapter, Engine und SQLite.
Die Daten liegen getrennt von persönlichen Beobachtungen. Alle elf Prüfungen
meldeten `ok`; es wurden keine Testangebote eingespeist.

| Einkaufsfall | Tatsächlich beobachtet | Verlauf und Alarm |
|---|---|---|
| Nicer Dicer Fusion Smart, 16-teilig | Namenssuche bei MediaMarkt, ALTERNATE und BerryBase ohne Treffer. [Lidl-Link](https://www.lidl.de/p/genius-nicer-dicer-nicer-dicer-fusion-smart-16-teilig-1-25-l-auffangbehaelter-klingen-aus-edelstahl/p100227834): 43,99 € + 5,95 € Versand, EAN 4023012338110, **nicht verfügbar**. | Zwei echte Beobachtungen mit geschlossenem und neu geöffnetem Prozesszustand; kein Kaufalarm. Die Lücke bei einem bestellbaren 16er-Set bleibt. |
| 8BitDo Ultimate 2C | Gemeinsame Suche liefert acht relevante Angebote: sechs MediaMarkt- und zwei BerryBase-Varianten. Wireless/Windows, Wired und Bluetooth/Switch bleiben getrennt. [Grüner Wireless-Controller bei MediaMarkt](https://www.mediamarkt.de/de/product/_8bitdo-ultimate-2c-gamepad-grun-157963406.html) und [Saturn](https://www.saturn.de/de/product/_8bitdo-ultimate-2c-gamepad-grun-157963406.html): jeweils 42,60 € inklusive Versand, x-kom, verfügbar, EAN 6922621505990. | Beide Direktlinks gespeichert und nach Neustart erneut abgefragt. Händlervergleich anhand derselben EAN. Je ein lokales Ereignis beim Wunschpreis 45 €; keine Wiederholung bei unverändertem Preis. |
| Waschmaschine ca. 8 kg, maximal 400 € | Gemeinsame Suche bei MediaMarkt und ALTERNATE: [Haier HW80-BP14637T G3](https://www.mediamarkt.de/de/product/_haier-hw80-bp14637t-g3-waschmaschine-8-kg-1330-umin-a-3035406.html), 339 € + 39,90 € = **378,90 €**, verfügbar. Zusätzlich [KOENIC KWM 8142 A-10 INV](https://www.mediamarkt.de/de/product/_koenic-kwm-8142-a-10-inv-waschmaschine-8-kg-1351-umin-a-3014762.html) direkt bestätigt: 299,99 € + 39,90 € = **339,89 €**, verfügbar. | KOENIC zweimal frisch über Direktlink beobachtet. Ein lokales Ereignis beim Wunschpreis 400 €. Optionale Anschluss-, Entsorgungs- und Garantieleistungen werden nicht in den regulären Kaufpreis eingerechnet. |

Alle vier exakten Aufträge besitzen zwei echte Preisbeobachtungen. Drei lokale
Wunschpreisereignisse vor und nach Neustart, `PRAGMA integrity_check = ok`.
Es wurde kein tatsächlicher Preisrückgang während des kurzen Live-Fensters
beobachtet; Rückgänge nach längeren Pausen werden synthetisch getestet.
Die vollständigen Rohdaten verbleiben lokal unter dem oben genannten Artefaktordner.

## Optionale Anbieter: Live-Demo und Adapterprüfung getrennt

Im offiziellen [ReefAPI-Playground](https://reefapi.com/playground) wurden echte
Anfragen ausgeführt (`mode=live`, `cache_hit=false`). Keine Anmeldung oder
Verwendung des Demo-Zugangs für laufende Überwachungen.

| Quelle | Live-Antwort | Adapterauswertung der aufgezeichneten Antwort |
|---|---|---|
| Amazon DE | Suche nach 8BitDo: fünf Treffer. ASIN B0D736BCNM: 32,42 €, Retro Games Europe, Mint, verfügbar. | Exakte ASIN bestätigt; Versand unbekannt. Angezeigter Gratisversand galt teils nur für qualifizierte Erstbestellungen. Keine daraus erfundenen Gesamtpreise oder Kaufalarme. |
| eBay DE | Suche: fünf Treffer. Artikel 127510636158: 29,90 €, berrybase, blau, EAN 6922621506355, aktiv. Anbieter meldet 4,95 € Versand für seine voreingestellte PLZ 10115. | Vier eindeutige Festpreisreferenzen aus der Suche. Versand wird nur bei Übereinstimmung mit einer ausdrücklich vom Nutzer eingetragenen Liefer-PLZ übernommen. Unbestätigte Varianten und Auktionen werden abgewiesen. |
| Galaxus DE | 24 Suchzeilen; Produkt 49481549: 30,34 €, verfügbar, grün, EAN 6922621505990, MPN RET00470. | Elf passende Referenzen. Exakte Identität stimmt mit MediaMarkt/Saturn überein; Versand unbekannt, deshalb kein Gesamtpreisvergleich oder Kaufalarm. |
| OTTO | Nicer-Dicer-Suche meldet `TARGET_BLOCKED`. | Kein Adapter und kein behaupteter Treffer. |
| Kaufland | Die öffentliche Demo stellt für den Suchendpunkt keine editierbaren Deutschland-Parameter bereit. | Kein verifizierter deutscher Abruf, kein Adapter. |

Die gespeicherten Antworten wurden durch die echten Parser verarbeitet. Das ist
**kein Ende-zu-Ende-Nachweis des API-Transports mit einem persönlichen Schlüssel**.
Amazon, eBay und Galaxus sind implementiert, bleiben gemäß Nutzerwunsch deaktiviert.
Providerbedingungen, Händlerrechte und Grenzen stehen in
[Quellenübersicht](phase3-sources.md) und [Zugangsprüfung](partner-access.md).

## Automatisierung und Oberfläche

- Vollständige synthetische Suite: **341 Tests bestanden**, einschließlich Preisrückgang nach Pause, Quellenisolation, Neustart, Benachrichtigungsduplikate und bestehenden Migrationstests. Ein bestehender Starlette/httpx-Deprecation-Hinweis.
- Neue Regressionen: Variante gegen Gruppenpreis/-bestand, Mitgliederpreise, Verkäufer/Versand, sichere Kategorieverweise samt robots-Prüfung, ASIN-/Artikel-/EAN-Verwechslungen, eBay-Auktionen und Varianten, API-Cachealter und persistentes Tagesbudget.
- `compileall`, `node --check dealchecker/static/app.js` bestanden.
- Windows/Python 3.14: echte lokale Anwendung gestartet. Im Browser MediaMarkt-Link aufgelöst, Produkt übernommen, Wunschpreis gespeichert, geprüft und lokales Ereignis angezeigt. Neustart erhält den Auftrag. Reef-Schlüsselformular mit eindeutig synthetischem Wert gespeichert, leere Passwortanzeige geprüft und Wert wieder entfernt; keine Anbieterabfrage damit.
- Geheimnisse liegen außerhalb von Git und SQLite. API-Status/Validierungsfehler geben den Schlüssel nicht zurück. Budget bleibt über Neustart, Schlüsselwechsel und Entfernen erhalten; fehlgeschlagene Aufrufe zählen konservativ. Keine automatische Kostenerweiterung.
- Unabhängiger Sol-Review führte zu Korrekturen bei Variantenpreisen, Cachealter und widersprüchlichem eBay-Bestand; die entsprechenden Regressionen sind enthalten. Linux-Runtime wurde lokal nicht gestartet; Betriebssystemmatrix wird separat in GitHub CI geprüft, sofern verfügbar.

Die Händlerabdeckung ist weiterhin begrenzt. Nächste fachliche Lücke sind ein
bestellbares Nicer-Dicer-16er-Set sowie kontofreie Zugänge für OTTO/Kaufland.
