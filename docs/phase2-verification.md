# Phase 2: Prüfstand vom 8. November 2026

Basis: gemergtes MVP `014990068ac41e95e679dad9fc01ad24e299ea54`; Feature-Branch `feature/phase2-market-coverage`. Frühere [Review-Fixes](review-fixes.md) bleiben als historische Nachweise erhalten.

## Automatisierte Prüfungen — synthetische Daten

**167 Tests bestanden** unter Windows/Python 3.14.6. `python -m compileall -q dealchecker`, `python -m pip check`, `node --check dealchecker/static/app.js` und `git diff --check` erfolgreich. Eine bestehende Starlette-Testclient-Deprecation-Warnung bleibt; kein Testfehler.

Gezielte neue Prüfungen decken ab:

- Discovery gegenüber exakter Überwachung, Normal-/Black-Week-/Quellenüberschreibungen, Quellenminimum, Migration bestehender Intervalle und Neustart.
- Black-Week-Aktivierung, Ablauf und laufende Quellenfehler; eine unmittelbar zuvor geänderte Suchanfrage bleibt fällig. Schema 6 speichert diesen ausstehenden Abruf ausdrücklich. Eine weitere unabhängige Reproduktion prüfte Ablauf, Neustart, erhöhtes Quellenminimum und anschließendes Zurücksetzen des Merkers.
- Verkäufer-, Varianten-, Zustand- und Packungstrennung; GTIN-Prüfziffer/Normalisierung; konservative Vergleiche zwischen zwei Händlern; kein Zusammenführen ähnlicher Namen oder Referenzpreise.
- Bestenvergleich-Suche/ID/EAN/URL, Identitätskonflikte, ungültige Preise, Cache und Ausfälle. Vollständiger lokaler API-Pfad: entdecken → exakt übernehmen → Neustart → niedrigerer Referenzpreis wird gespeichert, aber löst keinen Kaufalarm aus.
- Modula24-Suche → exakte Variante → Neustart → gezielte Produkt-/Versandabfrage ohne erneuten Gesamtkatalog. Ein kontrollierter Preisrückgang löst einen Alarm aus. Unbekannter Versand, falsche Varianten/Währungen und Cachealter werden getestet.
- Bestehende mehrstündige Pausen, dauerhaft defekte Parallelquellen, Sicherheits-, Persistenz- und Zustelltests bleiben enthalten.

Die GitHub-Workflow-Matrix prüft Windows und Ubuntu jeweils mit Python 3.11 und 3.14; der konkrete Lauf ist am Feature-PR sichtbar. Lokale Tests ersetzen diesen Linux-Lauf nicht.

## Echte Live-Prüfungen — keine Fixtures

Die tatsächlichen Adapter mit gemeinsamem HTTPS-Client und anschließend der lokale Windows-Dienst auf `127.0.0.1:8766` nutzten ein neues isoliertes Datenverzeichnis. Kein bestehender Nutzerbestand wurde verändert, kein Discord-Versand aktiviert.

| Quelle / Ablauf | Tatsächlich beobachtet | Aussagegrenze |
|---|---|---|
| Bestenvergleich, `Airfryer` | 23 bepreiste Referenztreffer im Adapter und vollständigen lokalen API-Pfad. | Höchstens 25 API-Treffer je Suchbegriff; keine Vollständigkeitsgarantie. |
| Bestenvergleich, `Bosch` | 14 Referenztreffer, darunter Backofen-/Kochfeldkombinationen. | Keine verifizierten einzelnen Händlerangebote. |
| Bestenvergleich, `Samsung` | Keine nutzbaren Treffer in dieser Probe. | Kein Anspruch auf umfassende Elektronikabdeckung. |
| Bestenvergleich, gezielt | `9184068`, GOURMETmaxx Airfryer, EAN `4067522012115`, 39,99 € Artikelreferenz. Auswahl über „Exakt beobachten“ in der UI, später erneute gezielte Abfrage nach Prozessneustart erfolgreich. | Versand/Verkäufer/Bestand unbekannt; trotz Wunschpreis 50 € kein Alarm. `offer_count=5`, jedoch `offers=[]` in der Detailprobe. |
| Modula24, `Konnektor` | Fünf reale Treffer. | Nur dieser Händler und dessen Möbelteile-/Werkzeugkatalog. |
| Modula24, Variante `58436517069064` | Gezielte Produkt-JSON-Abfrage plus lesendes DE-Versandangebot: 1,50 € Artikel + 5,22 € DHL = 6,72 €, verfügbar. Lokales Wunschpreisereignis bei Grenze 10 €. | Ein Stück, Deutschland, dokumentierte DHL-Option; keine Warenkorb-/Mengenoptimierung. |
| Modula24, Variante `58436497080584` | Rohr 2,50 € + 5,22 € DHL; Variante `Länge: 100` stimmt zwischen Feed und gezielter Abfrage überein. | Eigene begrenzte Adapterprobe, keine allgemeine Variantenabnahme des gesamten Katalogs. |
| Apple, `Monument Valley` | Vier tatsächliche DE-App-Suchtreffer; bestehender Adapter funktioniert weiterhin. | App-Kaufpreise einschließlich kostenloser Sticker-App, keine physischen Waren/Abos/In-App-Käufe. |

Nach dem Windows-Prozessneustart blieben **fünf Aufträge, 34 gespeicherte Treffer und ein Ereignis** sowie sämtliche Fälligkeiten erhalten. Beide exakten Live-Aufträge ließen sich erneut abfragen; kein Doppelalarm. Die 34 Einträge enthalten bewusst auch denselben gefundenen Artikel im Such- und im exakten Auftrag, also nicht 34 verschiedene Produkte.

## Oberfläche und unabhängige Prüfung

Im tatsächlichen Browser geprüft: Referenzkennzeichnung, Übernahme von Produkt-ID/URL/EAN bei leerer Suchanfrage, Eintragen eines Wunschpreises, Pflichtpaar für Quellenintervalle, Abwahl einer Quelle ohne verwaiste Überschreibungen, wirksames Quellenminimum (600 Sekunden gewünscht, 1.200 wirksam), Aktivierung/Deaktivierung für sieben Tage und Referenzverlauf mit unbekanntem Gesamtpreis. Keine Browser-Konsolenfehler in der Prüfung. Ein Screenshot und lokale Antwortbelege liegen im ignorierten Verzeichnis `artifacts/phase2-live-20261108-1644/`.

Sol prüfte Adapter/Netzwerk sowie Kernlogik unabhängig; Luna implementierte Quellenübersicht und UI, ein weiterer Worker prüfte die UI. Der gefundene Randfall beim Ende der Black Week wurde vor Abschluss korrigiert und erneut unabhängig reproduziert.

## Verbleibende Grenzen

**Breite reale Händlerpreisalarme für den deutschen Markt sind damit noch nicht erreicht.** Die neue breite Quelle liefert Referenzen; der einzige direkt nutzbare physische Händler mit bekannten Gesamtpreisen bleibt Modula24. Es bestehen keine Partnerzugänge, wie vom Nutzer bestätigt. [20-Quellen-Matrix](phase2-sources.md) und [konkrete Freischaltungsschritte](partner-access.md) nennen die nächsten Wege.

Ein reales identisches Produkt bei mehreren aktiven Händlern konnte deshalb nicht geprüft werden. Vergleichsgruppen und Verkäufertrennung wurden ausschließlich mit als synthetisch markierten Daten getestet. Ebenso sind kontrollierte Preisänderungen kein Beleg einer tatsächlich während der Sitzung erfolgten Händlerpreissenkung. Kein Raspberry-Pi-Deployment, mehrtägiger Dauerlauf, echter Discord-Versand oder Kauf wurde durchgeführt.
