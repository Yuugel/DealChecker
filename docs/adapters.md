# Neue Datenquellen anbinden

Vor einer Integration den zulässigen Datenzugang, Nutzungsbedingungen, Kosten, Rate Limits und praktische Preisqualität prüfen. Technische Erreichbarkeit allein ist keine Nutzungserlaubnis. Keine CAPTCHA- oder Sperrumgehung.

## Händlerfeed ohne neuen Code

Ein ausdrücklich freigegebener öffentlicher JSON-Katalog kann mit `DEALCHECKER_FEEDS` hinzugefügt werden. Format, Beispiel und Grenzen stehen in [sources.md](sources.md). Eine solche Konfiguration ist die Bestätigung des Betreibers, dass er den Feed nutzen darf; DealChecker kann keine individuelle Feed-Lizenz prüfen. Keine Geheimnisse in Feed-URLs. Authentifizierte Feed-APIs benötigen einen eigenen Adapter mit separater, sicherer Zugangsdatenkonfiguration.

## Eigener Adapter

Eine Klasse in `dealchecker/adapters/` implementiert:

```python
class NewSource:
    id = "new_source"
    name = "Meine Quelle"
    description = "Genau benannte Abdeckung und Einschränkungen"
    min_interval_seconds = 300

    async def search(self, watch):
        # Echte neue Suche; query und höchstens fünf Synonyme verarbeiten.
        # Rückgabe: list[dealchecker.models.Offer]
        ...

    async def lookup(self, watch):
        # Verifizierte Produktidentität; Mehrdeutigkeit als Fehler melden.
        ...
```

In der Adapterregistrierung `build_registry()` ergänzen; Scheduler, Bewertung, Datenbank und UI benötigen keine quellenspezifischen Änderungen. Den injizierten `SafeHttpClient` verwenden, feste erlaubte Hosts angeben und Anbieterlimits gegebenenfalls weiter verschärfen. Ergebnisse cachen, ohne Cachetreffer als neue Erhebung zu datieren. Der Kern begrenzt eine gesamte Prüfung auf 60 Sekunden und höchstens vier parallele Überwachungen.

`Offer` verlangt EUR-Cent als ganze Zahlen, Händler, stabile Produkt-ID, Produktname, sichere Angebotsadresse, Verfügbarkeit, Prüfzeitpunkt und Versandkosten (explizit `None`, wenn unbekannt). Variante, EAN/Modell, Hersteller, Kategorie, Gutschein und Grundpreis nur bei belegten Daten befüllen. Herkunft und Qualitätsstatus müssen ehrlich sein. Kein Rückschluss aus fehlendem Preis auf ein kostenloses Angebot.

Ein Fehler wird als erklärender `ValueError`/`RuntimeError` ohne Token, URL-Geheimnisse oder vollständigen Antworttext gemeldet. Eine erfolgreiche leere Suche ist kein Netzwerkfehler. Bei mehreren Quellen bleiben erfolgreiche Teilresultate erhalten, während der Auftrag einen sichtbaren Teilfehler meldet.

## Abnahme eines Adapters

Synthetische Fixtures für Suche, ID-Abfrage, Varianten, unbekannten Versand, Ausverkauf, ungültigen Preis, nicht unterstützte Währung, Zeitüberschreitung und leere Antwort hinzufügen. Die Tests müssen echte Normalisierung und Kernintegration ausführen. Separat einen begrenzten Live-Abruf dokumentieren: Datum, Quelle, tatsächlicher Endpunkt und beobachtetes Ergebnis. Kontrollierte Fixture-Preisänderungen sind kein Nachweis einer echten Händlerpreisbewegung.
