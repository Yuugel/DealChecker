# Amazon DE ohne API-Konto, mit wählbarem Prime-Preis

Stand: 9. November 2026. Der Quellenbezeichner `amazon` liest öffentliche
Amazon.de-Seiten direkt. Kein ReefAPI-, Partner- oder Amazon-Konto, kein Login
und keine zusätzlichen Kosten sind dafür eingerichtet oder erforderlich.
Die frühere Aussage, Amazon sei nur über einen API-Anbieter möglich, war zu eng.

## Benutzung

Einen vollständigen `https://www.amazon.de/dp/ASIN`-Produktlink oder einen
Suchbegriff eingeben. Für Mitglieder gibt es **„Ich habe Amazon Prime“** beim
Schnell-Hinzufügen und **„Amazon Prime verwenden“** in der Beobachtung. Die
Auswahl wird pro Beobachtung gespeichert und über Neustarts erhalten.
Ohne bestätigten Prime-Sonderpreis wird ausdrücklich der reguläre Preis gezeigt.
Das erfordert keine Weitergabe von Amazon-Anmeldedaten an DealChecker.

Prime-Preise werden nur aus einer ausdrücklich als Prime gekennzeichneten
Preisoption übernommen, deren ASIN und Verkäufer mit dem geprüften Angebot
übereinstimmen. Sie tragen die Kennzeichnung `price_basis=prime` in Oberfläche,
Ereignis und Benachrichtigung. Ihr Verlauf wird getrennt gespeichert: Der Wechsel
von regulär zu Prime ist kein Preisrückgang. Gutscheine, Spar-Abos,
Business-Preise, Gebrauchtangebote und Ratenzahlungen ersetzen keinen Kaufpreis.

**Versand bleibt eigenständig:** Prime bedeutet nicht automatisch kostenlosen
Versand. Wenn kein eindeutiger Versandbetrag für die gewählte Preisoption
vorliegt, zeigt DealChecker Artikelpreis und Verlauf, aber keinen Gesamtpreis
und keinen Kaufpreisalarm. Erstbesteller-/Mindestbestellbedingungen werden nicht
als allgemeiner Gratisversand behandelt. Ein bestätigter Gesamtpreis kann wie
bisher Wunschpreis- und Preisrückgangsalarme auslösen.

## Umfang und Grenzen

- Produktsuche über das öffentliche Suchformular, höchstens sechs Detailseiten
  pro Durchlauf. Suchkarten dienen nur zur Entdeckung; Preise stammen aus
  bestätigten Detailseiten. Defekte einzelne Treffer werden als Teilfehler
  ausgewiesen; Robots-/Zugriffssperren werden nicht übersprungen.
- Exakte Überwachung anhand der ASIN, kanonischem Link, Kaufbox, Verkäufer und
  aktuell ausgewählter Variante. Kein Zusammenführen ähnlicher Namen.
  Verkäuferwechsel erzeugen getrennte Angebotsverläufe. Andere Marketplace-
  Verkäufer als die geprüfte Kaufoption werden nicht vollständig erfasst.
- Standardintervall für normale exakte Amazon-Beobachtungen: zwei Stunden;
  Black Week: 30 Minuten. Discovery: acht bzw. drei Stunden. Konfigurierbare
  Intervalle, mindestens 30 Minuten Cache, mindestens 3,2 Sekunden Hostabstand,
  robots.txt/Crawl-delay und maximal 4 MiB pro HTML-Antwort gelten weiterhin.
  Cachetreffer werden nicht als neue Preisbeobachtungen ausgegeben.
- Bei CAPTCHA, 403, Robots-Sperre oder unverständlichem HTML endet die Abfrage.
  Keine Browser-Tarnung, Proxyrotation, Login-Übernahme oder kostenpflichtige
  Ausweichroute. HTML-Änderungen können die Integration unterbrechen.
- Die technische Abrufbarkeit ist **keine API-Lizenz oder Händlerfreigabe**.
  Amazon veröffentlicht unter [Lizenz und Zugriff](https://sell.amazon.de/datenschutzerklaerung)
  Einschränkungen zur automatisierten Datensammlung. Die separate deutsche
  Shop-Bedingungsseite war bei der Probe mit HTTP 406 nicht abrufbar. Es wird
  keine rechtliche Freigabe für diese private Anwendung behauptet; robots.txt
  ersetzt keine Nutzungsbedingungen. Es erfolgt keine öffentliche Katalogkopie.

## Echte Abrufe und getrennte Regressionstests

`python scripts/verify_amazon_direct.py --output artifacts/NEUER-ORDNER` führt
einen ausdrücklich gestarteten Live-Test in einer separaten SQLite-Datei aus.
Keine persönlichen Aufträge, API-Schlüssel oder Benachrichtigungskanäle werden
geladen. Lokaler Nachweis: `artifacts/amazon-live-20261109-a/report.json`,
9. November 2026, 00:06–00:07 Uhr MEZ.

| Prüfung | Tatsächlicher Befund |
|---|---|
| Suche „8BitDo Ultimate 2C“ | Fünf bestätigte Angebote: ASINs B0DNM3VGGG, B0D736BCNM, B0DNJLPPPM, B0D72WYT8Z, B0DNYMZX41. Ein weiterer Treffer ohne eindeutigen Verkäufer wurde als Teilfehler ausgelassen. Kein Suchkartenpreis als Angebot übernommen. |
| [8BitDo Mint, B0D736BCNM](https://www.amazon.de/dp/B0D736BCNM) | 32,42 €, Verkäufer Retro Games Europe, Mint, verfügbar. Versand unbekannt; kein Gesamtpreisalarm. Die als Modellnummer gelieferte Zahl wird nicht ungeprüft in eine EAN umgedeutet. |
| [EasySMX X20 weiß, B0DCFG91SV](https://www.amazon.de/dp/B0DCFG91SV) | 55,99 € regulär, ausdrücklich kostenfreie primäre Lieferung, EasySMX EU Official, verfügbar. Gewähltes Modell X20 und Variante weiß. |
| Prime beim EasySMX | Ein früherer echter HTML-Abruf vom 8. November enthielt zusätzlich 49,83 € Prime; Offline-Wiedergabe bestätigt diese separate Preisoption, Versand unbekannt. Die Live-Abrufe nach Mitternacht enthalten keine Prime-Preisoption mehr und liefern korrekt den regulären Preis. Ursache der Änderung unbekannt. Die 49,83 € sind daher **kein aktueller Live-Preis**. |
| Neustart und Verlauf | Drei exakte Beobachtungen (8BitDo, EasySMX regulär, EasySMX mit Prime-Präferenz) nach vollständigem Schließen/Neuöffnen aller Ressourcen erneut abgefragt; jeweils zwei echte Beobachtungen, SQLite-Integrität `ok`. |
| Kaufereignisse | Zwei lokale Zielpreisereignisse für die beiden getrennten EasySMX-Aufträge zum bestätigten regulären Gesamtpreis; nach Neustart keine Verdoppelung. Kein Ereignis für unbekannten Versand. Keine echten Preisrückgänge im Prüfzeitraum behauptet. |

Automatisierte synthetische Tests prüfen abweichende ASIN/Variante/Verkäufer,
mehrdeutige Preise, Prime-Auswahl und Cache-Trennung, bedingten Versand,
CAPTCHA-Pause, Teilfehler, fehlenden Providerzugang, Intervallvorrang,
Bestandsdatenbanken ohne Prime-Feld, getrennte Preisverläufe und
Benachrichtigungskennzeichnung. Die bestehende Suite prüft weiterhin
Quellenausfall-Isolation und Preisrückgänge nach längeren Pausen.

Validierung: **361 automatisierte Tests** unter Windows/Python 3.14 bestanden,
`compileall` und JavaScript-Syntaxprüfung erfolgreich. Die Browserprüfung
löste den echten EasySMX-Link mit Prime-Auswahl auf, speicherte die Beobachtung
und bestätigte den aktivierten Schalter nach vollständigem App-Neustart.
Der unabhängige Code-Review fand keine weiteren konkreten Fehler; die Grenze
bei unbekanntem Prime-Versand ist in Vorschau, Angebotskarte und hier sichtbar.

Der frühere Prime-HTML-Beleg bleibt lokal unter
`artifacts/amazon-easysmx-prime-capture-20261108.html` erhalten, SHA-256
`607ffad127a7f48c97e52b1bff3948c56568a945be8f590aa0ad7bd614f5e317`.
`artifacts/amazon-prime-replay-report.json` kennzeichnet die erneute Auswertung
ausdrücklich als Offline-Wiedergabe statt aktueller Preisprüfung.
