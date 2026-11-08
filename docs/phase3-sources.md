# Phase 3: tatsächlich nutzbare Händlerzugänge

Stand: 8. November 2026. Ziel ist eine private lokale Anwendung ohne Publisherkonto. Keine Konten, Partnerschaften oder kostenpflichtigen Dienste wurden aktiviert. Die [20-Quellen-Matrix](phase2-sources.md) dokumentiert Phase 2; die folgenden Zugänge ergänzen und korrigieren ihren damaligen Stand.

| Quelle | Implementiert und live geprüft | Grenzen |
|---|---|---|
| ALTERNATE | Öffentliche Shopsuche nach Namen, EAN und bestätigter Modellnummer; gezielter Abruf des Produktlinks. Elektronik, PC-Zubehör, Haushaltsgeräte. Live: RTX 5070, Logitech MK235, Waschmaschinen. | Höchstens sechs Produktdetailseiten pro Suchdurchlauf, kein vollständiger Katalog. Nur eindeutiges Einzelangebot mit bestätigtem Händler. DE-Versand nur bei eindeutigem Betrag ohne unaufgelöste Bedingungen. |
| BerryBase | Öffentliche Suche und Produktlinks; Elektronik, Controller, Bastelzubehör. Live: 8BitDo Ultimate 2C Bluetooth in Pink und Blau, getrennte EAN/Varianten. | Aktuelle Produktseiten liefern keinen bestätigten Versandbetrag. Gesamtpreis bleibt unbekannt; kein Kaufpreisalarm. Bluetooth/Switch wird nicht als Wireless/Windows ausgegeben. |
| Lidl DE | Konkrete Produktlinks für eindeutige Online-Einzelartikel; Küche, Haushalt, Werkzeuge. Live: Nicer Dicer 16-teilig und Parkside Bandschleifer. | Keine freie Suche: robots.txt sperrt den Suchpfad. Variantenfamilien/mehrdeutige Links werden abgewiesen. Versand nur aus dem übereinstimmenden Hauptartikel ohne XL-/Speditionszuschlag. Ohne Verkäufermetadaten bleibt Verkäufer unbekannt. |
| Modula24, Apple, Bestenvergleich | Bestehende Integrationen erhalten: Möbelteile, App-Kaufpreise bzw. breite Preisreferenzen. | Bestenvergleich bestätigt keine bestellbaren Händlerangebote. [Bestehende Quellenregeln](sources.md). |

ALTERNATE und BerryBase veröffentlichen normale Suchformulare und strukturierte Produktdaten. Die geprüften [ALTERNATE-robots](https://www.alternate.de/robots.txt), [ALTERNATE-AGB](https://www.alternate.de/HILFE/ALTERNATE/AGB), [BerryBase-robots](https://www.berrybase.de/robots.txt) und [BerryBase-AGB](https://www.berrybase.de/agb) ergaben keine Sperre der verwendeten öffentlichen Produkt-/Suchpfade bzw. kein erkanntes ausdrückliches Verbot dieser begrenzten privaten Abfragen. Das ist **keine ausdrückliche API-Lizenz oder Garantie dauerhafter Nutzbarkeit**. Die Anwendung veröffentlicht keine Shopkataloge. Lidl-Nachweise und weitere große Händler stehen im [Zugriffsbericht](phase3-major-access.md).

Alle drei HTML-Adapter prüfen robots.txt vor dem Zugriff, beachten passende Allow-/Disallow-Regeln und Crawl-delay und stoppen bei nicht prüfbaren Regeln oder Zugriffssperren. Fester Host, öffentliche DNS-Ziele, HTTPS, keine Weiterleitungen, 2-MiB-Antwortlimit, 16-KiB-Headerfeldlimit, mindestens 3,2 Sekunden Hostabstand sowie mindestens 30 Minuten Angebots-/Suchcache. Die 30 Minuten sind eine eigene konservative Grenze, kein versprochenes Händlerkontingent. Robots wird spätestens nach sechs Stunden erneut geprüft. Cachetreffer behalten ihren echten Beobachtungszeitpunkt. Bedingte Mitglieder-/Mengen-/Abopreise und ungültige Preiszeiträume werden nicht als normaler Kaufpreis übernommen.

Die allgemeine [BerryBase-Versandtabelle](https://www.berrybase.de/versand-und-zahlungsbedingungen) ersetzt keinen belegten Produktversand. Eine pauschale Zahl wird deshalb nicht auf Controller oder Sperrgut übertragen. Standardlieferung bei ALTERNATE kann nur bis zur Bordsteinkante gelten; Aufstellung/Anschluss sind nicht enthalten.

## Große noch offene Händler

| Händler / Weg | Grenze und nächster sinnvoller Schritt |
|---|---|
| MediaMarkt, Saturn | Öffentliche Produktseite und robots separat geprüft; lokale Abrufe liefern Challenge/403. Offizielle Affiliatewege benötigen Freigaben. Kein Browser zur Umgehung der Challenge. Für Käufer stehen offizielle App-Preisalarme zur Verfügung; Links im Zugriffsbericht. |
| OTTO | Öffentlicher Produktabruf ohne verwertbares Ergebnis; Kundenbedingungen beschränken Datenvervielfältigung. Zuerst ausdrückliche Erlaubnis für den privaten Tracker, danach echten Feed/Abruf prüfen. |
| Galaxus | Websitebedingungen verbieten systematische automatisierte Abrufe. Tradedoubler ist für diesen privaten Anwendungsfall nicht automatisch freigegeben. Offizielle Wunschlistenalarme sind unmittelbar nutzbar. |
| Kaufland | Aktuelle robots lokal durch Challenge nicht prüfbar; privates Downloadrecht ersetzt keine verifizierte laufende Integration. Autorisierten Datenweg oder ausdrückliche Freigabe klären. |
| notebooksbilliger, Cyberport, Reichelt | Robots-Stichproben: 403, 403 bzw. 503; kein verifizierter öffentlicher Direktzugang. Dokumentierte Partnerwege bleiben freigabepflichtig. |
| Caseking, Pollin, Coolblue | Caseking sperrt Such-/demandware-Pfade; Pollin sperrt Querypfade; Coolblue-robots lokal 403. Keine versteckten Schnittstellen oder Ersatz-User-Agents zur Umgehung verwendet. |
| eBay | [Browse API](https://developer.ebay.com/develop/api/buy/browse_api) mit [Produktionsvoraussetzungen](https://developer.ebay.com/api-docs/buy/static/buy-requirements.html). Kein Schlüssel/Produktionszugang vorhanden. Zuerst Eignung der privaten Anwendung und Produktionsfreigabe bestätigen, dann DE-Versand/Verkäufer live testen. |
| Amazon | Kein automatischer Seitenabruf. [Creators API](https://partnernet.amazon.de/creatorsapi/docs/en-us/introduction) ist an Associates-Qualifikation/Freigabe gebunden. [Keepa API](https://keepa.com/api-docs/plans-tokens.html) erfordert ein kostenpflichtiges Token-Abonnement; Anwendungsrechte/Preisumfang vor einer gesondert genehmigten Aktivierung prüfen. Kein aktiver Platzhalteradapter. |

Ein einzelner HTTP-Fehler beweist kein grundsätzliches Integrationsverbot. Die Tabelle unterscheidet technische Nichtverfügbarkeit, ausdrückliche Bedingungen und noch fehlende Zulassung. Es gibt keine behauptete Abdeckung der großen Marktplätze und keine aus Suchindex-Snippets übernommenen Live-Angebote.
