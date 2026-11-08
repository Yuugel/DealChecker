# PR #1: Review-Korrekturen vom 8. November 2026

Grundlage ist der [vollständige externe Review-Kommentar](https://github.com/Yuugel/DealChecker/pull/1#issuecomment-6722334167) zu `ef12e04`. Alle drei Befunde wurden am Code bestätigt. Die frühere Prüfung mit 83 Tests bleibt in [verification.md](verification.md) als historischer Nachweis erhalten.

## Korrekturen

1. **Physische Produkte:** Modula24 ist jetzt als reale Standardquelle registriert. Seine ausdrücklich freigegebene, kostenlose API liefert einen endlichen Katalog mit Varianten, EUR-Preisen, Verfügbarkeit und Versand nach Deutschland. Discovery durchsucht diesen Katalog; exakte Aufträge prüfen dieselbe Varianten-ID erneut. Cache: fünf Minuten; feste HTTPS-Quelle, maximal 4 MiB und 5.000 Varianten. Die Abdeckung ist auf Möbelteile, Zubehör und Werkzeuge dieses Händlers begrenzt. Es gibt weiterhin keine allgemeine Abdeckung für Haushaltsgeräte, Elektronik, Küchenzubehör oder Lebensmittel. [Quelle, Nutzungsfreigabe und Grenzen](modula24-source.md).
2. **Preisalarme nach Pausen:** Die vorherige geeignete Beobachtung muss nicht mehr frisch sein. Ein aktueller frischer, niedrigerer Preis kann nach Stunden und einem Datenbankneustart erneut alarmieren. Die Unterdrückung unveränderter Preise, Preiserhöhungen, Cache-Wiederholungen und veralteter neuer Daten bleibt erhalten.
3. **Quellenisolation:** Schema 4 speichert Fälligkeit, Fehler und Fehlversuche je Auftrag und Quelle. Gesunde Quellen behalten ihren normalen Takt; nur ausgefallene Quellen erhalten exponentielles Backoff. Die früheste Quellenfälligkeit bestimmt die nächste Auftragsprüfung. Noch nicht fällige Quellen werden nicht als neu geprüft behandelt. Parallele, zeitlich begrenzte Quellenabfragen verhindern, dass eine hängende erste Quelle die übrigen gar nicht erreicht.

Die Migration ist atomar, einschließlich Tabellenerstellung und Versionswechsel. Bei alten Sammelfehlern werden Quellen einmal zeitnah neu geprüft, statt den alten Fehler jeder Quelle zuzuschreiben. Erfolgreiche alte Aufträge behalten ihre Fälligkeit. Details stehen im [Kernbericht](review-core-report.md).

## Automatisierte Prüfung

- **116 Tests bestanden** unter Windows/Python 3.14.6, gegenüber 83 vor der Änderung. Eine vorhandene Starlette-TestClient-Deprecation-Warnung bleibt bestehen.
- `python -m compileall -q dealchecker`, `node --check dealchecker/static/app.js`, `python -m pip check` und `git diff --check`: erfolgreich.
- Neue Regressionen: vierstündige Beobachtungslücke mit SQLite-Neustart; ungeeignete Zwischenbeobachtungen; dauerhafter Ausfall einer von mehreren Quellen über mehrere Zyklen; hängende Quelle; Cache/Deduplizierung; Pause, Bearbeitung und Black Friday; Migration und Abbruch während der Migration; echte Adapterlogik mit synthetischen physischen Produkt-, Versand- und Variantendaten.
- Zwei zusätzliche API-Integrationstests durchlaufen die Anwendung samt Persistenz: gemischte gesunde/defekte Quellen nach Neustart sowie physische Discovery → exakte Auswahl → niedrigere Preisbeobachtung nach Neustart.

Sol implementierte Quelle und Kernkorrekturen in getrennten Worktrees, Luna aktualisierte die Quellenhinweise der Oberfläche. Eine weitere unabhängige Codeprüfung der Änderungen fand zwei Migrationsprobleme; beide wurden mit Regressionstests behoben. Die anschließende gezielte Nachprüfung meldete keine offenen Befunde im geprüften Umfang.

## Reale lokale Prüfung

Der tatsächliche Windows-Prozess `python -m dealchecker --port 8766` lief mit einer neuen, isolierten Datenbank unter `artifacts/review-live-20261108`. Es wurden keine synthetischen Quellen registriert und kein Discord-Webhook geladen.

- Modula24-Discovery „Konnektor“: **fünf reale Treffer**, darunter der Konnektor mit Varianten-ID `58436517069064`, 1,50 € Artikelpreis und 5,22 € Versand nach Deutschland, zusammen **6,72 €**.
- Aus der Suche ausgewählte exakte Variante `58436515823880`: Werkzeug Konnektorzieher, **27,99 € + 5,22 € = 33,21 €**. Beim gesetzten Prüf-Wunschpreis von 34,21 € entstand genau ein lokales Ereignis.
- Bestehende Apple-Quelle: Monument Valley, ID `728293409`, weiterhin **3,99 €**.
- Drei Aufträge, sieben Angebotszeilen, ein Ereignis. Dashboard, getrennte Artikel-/Versandkosten, Abdeckungshinweis und Quellenstatus im Browser sichtbar; keine erfassten Browserwarnungen oder -fehler.
- Nach echtem Prozessneustart blieben alle Daten und Fälligkeiten unverändert erhalten. Zum regulären Fünf-Minuten-Termin wurden beide Quellen automatisch erneut abgefragt: **sechs Prüfprotokolle, zwei reale Beobachtungen je Angebot, sieben frische Angebote und weiterhin genau ein Ereignis**. Es wurden keine Fälligkeiten manipuliert. Der temporäre Prüfprozess wurde danach beendet.

Die Belege liegen lokal im ignorierten Prüfverzeichnis. Eine echte Händlerpreisänderung wurde im Live-Fenster nicht beobachtet; Preisänderungen und vierstündige Pausen sind gezielt simulierte Regressionen. Kein externer Discord-Versand, kein mehrtägiger Dauer-/Lastlauf und kein Raspberry-Pi-Deployment. Windows/Linux-Kompatibilität wird zusätzlich durch die PR-CI auf Python 3.11 und 3.14 geprüft; deren Ergebnis steht im Review-Antwortkommentar. Die PR wird nicht selbst gemergt.
