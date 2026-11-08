# DealChecker

Lokale Preisüberwachung mit echter Produktsuche, nachvollziehbaren Preisverläufen und optionalen Discord-Alarmen. Python, SQLite und eine deutsche Weboberfläche; kein Cloudkonto und keine KI erforderlich.

**Tatsächliche Abdeckung:** **Modula24** liefert echte Möbelteile/Zubehör samt Varianten und Versand nach Deutschland. **Apple App Store DE** liefert App-Kaufpreise. Neu: **Bestenvergleich** entdeckt Produkte aus Elektronik, Haushalt und Küche und verfolgt deren Preisreferenzen gezielt. Diese API nennt keinen verifizierten Händler, Versand oder Bestand: Referenzen lösen **keine Kaufpreisalarme** aus. Amazon, MediaMarkt, Saturn, OTTO und andere große Händler sind ohne Partnerfreigabe weiterhin nicht angebunden. Weitere autorisierte JSON-Feeds werden unterstützt. [20 geprüfte Händler/Plattformen](docs/phase2-sources.md) · [Quellen und Grenzen](docs/sources.md).

## Windows: starten

1. Python **3.11 oder neuer** von [python.org](https://www.python.org/downloads/windows/) installieren; „Add Python to PATH“ aktivieren.
2. Dieses Repository herunterladen oder klonen.
3. **`start.cmd` doppelklicken.** Beim ersten Start wird im Projekt eine virtuelle Python-Umgebung eingerichtet. Dafür ist Internet nötig.
4. [http://127.0.0.1:8765](http://127.0.0.1:8765) im Browser öffnen.

Das Konsolenfenster muss für die Überwachung geöffnet bleiben. Mit **Strg+C** sauber beenden. Nach einem erneuten Start werden gespeicherte Überwachungen und fällige Prüfungen wieder aufgenommen. Im Ruhezustand oder bei ausgeschaltetem Rechner erfolgen keine Prüfungen.

Alternative in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e .
.\.venv\Scripts\python.exe -m dealchecker
```

## Erster Suchauftrag

„Überwachung hinzufügen“ → „Dynamische Suche“ → beispielsweise **Konnektor** oder **Kugel** → nur Quelle **Modula24** auswählen → speichern und „Jetzt prüfen“. Für Apps beispielsweise **Monument Valley** und „Apple App Store DE“ verwenden. Für Geräte **Airfryer** oder **Bosch** mit „Bestenvergleich · Preisreferenzen“ suchen und das Preislimit leer lassen. Neue Treffer stammen aus einer erneuten Quellensuche; sie müssen vorher nicht als URLs gespeichert sein.

Über **„Exakt beobachten“** am Treffer werden Quelle und Produktidentität direkt übernommen. Bei Modula24 kann ein gefundenes Produkt anschließend mit seiner **numerischen Varianten-ID**, etwa `58436517069064`, oder dem vollständigen Angebotslink einschließlich `variant` exakt überwacht werden. Die Produkt-ID in DealChecker entspricht der `variant_id` des Feeds; dessen `M24-…`-Artikelkennung und Lager-SKUs einer anderen API sind keine Ersatz-IDs. [Details und Grenzen](docs/modula24-source.md).

Für eine exakte Überwachung die numerische **App-ID** oder die deutsche `https://apps.apple.com/de/.../id…`-Produktadresse verwenden. Alternativ den vollständigen Produktnamen in **Suchanfrage** eintragen: Er wird nur bei einem eindeutigen, exakt passenden Titel akzeptiert. Der Anzeigename ist eine eigene Bezeichnung für die Überwachung. Apple unterstützt keine EAN-, Modell- oder Variantenprüfung. Für physische Produkte unterstützen entsprechend ausgestattete Händlerfeeds diese Felder.

Wunschpreise und Preisgrenzen gelten für den **Gesamtpreis einschließlich bekannter Versandkosten**. Bei unbekanntem Versand werden weder Gesamtpreisrabatte noch Wunschpreisalarme behauptet. App-Preise betreffen den einmaligen Downloadkauf; Abos und In-App-Käufe sind nicht enthalten.

## Funktionen

- Exakte Überwachung und eigenständige Discovery mit bis zu fünf Synonymen, Kategorien, Hersteller- und Ausschlussfiltern.
- Mehrere Angebote, persistente Beobachtungen, Preisdiagramm und lokale Ereignisübersicht.
- Breite Suche standardmäßig alle 8 Stunden; bekannte Produkte normal alle 2 Stunden, wichtige alle 30–45 Minuten. Normal- und Black-Week-Intervalle sind je Auftrag und Quelle konfigurierbar. Bestehende Aufträge behalten ihren bisherigen Takt.
- Black-Friday-Modus für 24 Stunden oder bis zu 7 Tage: Suche alle 3 Stunden, exakte Produkte je Priorität alle 5–60 Minuten. Quellencache und Anbieterlimits bleiben verbindlich; Fehler-Backoff bleibt je Quelle isoliert.
- Konservativer Händlervergleich für frische, lieferbare Angebote mit belegter Produktidentität und bekanntem Gesamtpreis; getrennte Historien für Verkäufer, Varianten, Zustand und Packungsgröße.
- Quellenfehler und veraltete Angebote sichtbar; fehlgeschlagene Prüfungen löschen die letzte Preisbeobachtung nicht.
- Deterministische Bewertung aus tatsächlich beobachteten Preisen. Keine erfundenen Marktpreise oder UVP-Rabatte.
- Discord-Webhook mit dauerhafter Versandwarteschlange und unterdrückten Wiederholungsmeldungen.
- Lokaler Einzelprozess, SQLite-Migration, geschützter HTTPS-Zugriff und ausschließlich Loopback als Standardadresse.

## Daten und Konfiguration

Standardmäßig liegen Daten unter **`%USERPROFILE%\.dealchecker`** (Linux: `~/.dealchecker`), getrennt vom Quellcode. Zum Sichern die Anwendung beenden und den vollständigen Ordner kopieren. Keine Datenbank während eines laufenden Schreibvorgangs isoliert kopieren.

Umgebungsvariablen vor dem Start setzen:

| Variable | Zweck |
|---|---|
| `DEALCHECKER_DATA_DIR` | Eigener Ordner für Datenbank und Instanzsperre |
| `DEALCHECKER_DISCORD_WEBHOOK` | Optionaler Discord-Webhook; anschließend Meldungen in den Einstellungen aktivieren |
| `DEALCHECKER_FEEDS` | JSON-Liste autorisierter öffentlicher Feed-Konfigurationen; siehe [Quellendokumentation](docs/sources.md) |

Zugangsdaten gehören weder ins Repository noch in Screenshots oder Fehlermeldungen. Die Anwendung liest den Webhook nur aus ihrer Prozessumgebung; sie stellt ihn nicht über die API bereit. Es gibt keinen verpflichtenden externen Dienst. Der Testlauf versendet keine echten Discord-Nachrichten.

[Bedienung und Alarmregeln](docs/usage.md) · [Adapter entwickeln](docs/adapters.md) · [Aktuelle Tests und Prüfgrenzen](docs/phase2-verification.md) · [Architektur](docs/superpowers/specs/2026-11-08-mvp-design.md)

## Linux / Raspberry Pi

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -e .
.venv/bin/python -m dealchecker
```

Keine Windows-spezifische Datenbank oder Browserabhängigkeit. Ein späterer systemd-Dienst kann denselben Befehl mit einem festen `DEALCHECKER_DATA_DIR` starten. Zugriff vom anderen Rechner beispielsweise über `ssh -L 8765:127.0.0.1:8765 pi@rechner`; der Server bleibt an Loopback gebunden. Ein Raspberry-Pi-Deployment wurde noch nicht ausgeführt.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q dealchecker
```

Die normalen Tests arbeiten ausschließlich mit deutlich gekennzeichneten synthetischen Antworten. Live-Prüfungen erfolgen separat und werden im [aktuellen Prüfbericht](docs/phase2-verification.md) dokumentiert; der [frühere Prüfstand](docs/verification.md) bleibt erhalten.
