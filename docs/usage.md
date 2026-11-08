# Bedienung und Grenzen

## Überwachungen

Eine exakte Überwachung prüft ein identifiziertes Produkt. Eine dynamische Suche fragt die gewählten Quellen mit Suchbegriff und Synonymen erneut ab und kann dadurch neue Produkte entdecken. Mehrdeutige exakte Titel werden nicht stillschweigend einem ähnlichen Produkt zugeordnet.

App-ID und Produkt-URL sind quellenspezifisch. EAN/GTIN, Modell und Variante können nur genutzt werden, wenn die Quelle sie liefert. Ein nicht unterstütztes Identitätsfeld führt zu einem sichtbaren Fehler. Quellenauswahl und Händlerfilter sind getrennt: Ein Feed kann mehrere Händler enthalten. Hersteller-, Kategorie- und Textfilter benötigen passende Quelldaten; eine unbekannte Angabe erfüllt den Filter nicht.

Die Preisobergrenze beschränkt Discovery-Ergebnisse. Der Wunschpreis steuert Alarme. Beide beziehen sich auf bekannte Gesamtpreise. Gutscheine und Grundpreise werden als Hinweise angezeigt; unbekannte Gutscheinbedingungen werden nicht automatisch vom Preis abgezogen. Unterschiedliche Packungsgrößen/Varianten werden nicht ohne belegte Identität miteinander verglichen.

## Bewertung

Die Anwendung betrachtet ausschließlich die eigene, gespeicherte Beobachtungshistorie desselben Angebots, Händlers und derselben Variante. Der erste Treffer kann den Wunschpreis erreichen, aber noch keinen historischen Tiefstpreis belegen.

1. Bekannter Gesamtpreis und lieferbares Angebot: Wunschpreis prüfen.
2. Strikt niedriger als jede frühere geeignete Beobachtung: neuer beobachteter Bestpreis.
3. Mindestens drei vorherige geeignete Beobachtungen und mindestens 10 % unter deren Median: sehr gutes Angebot.
4. Mindestens 5 % Rückgang gegenüber der letzten geeigneten Beobachtung: interessanter Preisrückgang.

Vergleichspreise sind **beobachtete historische Preise**, kein repräsentativer Marktpreis. Der angezeigte Vergleich und die Ersparnis müssen nicht dem Bewertungsgrenzwert entsprechen: Sie beziehen sich auf den vorher beobachteten Tiefstpreis. Erst längere Beobachtungszeiträume liefern eine brauchbare Grundlage. Veraltete Daten, unbekannte Versandkosten oder unbekannte/nicht verfügbare Ware erzeugen keinen Kaufpreisalarm.

Nach einem Alarm wird dasselbe Angebot nur bei einer weiteren relevanten Verbesserung unter dem zuletzt gemeldeten Preis und der vorherigen geeigneten Beobachtung erneut gemeldet. Unveränderte Preise, Preiserhöhungen und bloße Wiederkehr zum alten Preis erzeugen keine Doppelmeldungen. Ereignisse bleiben lokal verfügbar, auch wenn Discord nicht eingerichtet ist.

## Zeitplanung

| Priorität | Normal | Black Friday |
|---|---:|---:|
| Kritisch | 5 Minuten | 5 Minuten |
| Hoch | 15 Minuten | 7,5 Minuten |
| Normal | 30 Minuten | 15 Minuten |
| Niedrig | 60 Minuten | 30 Minuten |

Eigene Intervalle zwischen 5 Minuten und 24 Stunden sind möglich. Black Friday endet automatisch nach höchstens 24 Stunden. Cache, Zeitlimits, Anbieterlimits, parallele Aufträge und Fehler-Backoff können die tatsächliche Prüfung verzögern. Die manuelle Prüfung umgeht diese Schutzmaßnahmen nicht.

„Pause“ verhindert neue Prüfungen. Eine bereits laufende Anfrage kann noch beendet werden; Ergebnisse eines inzwischen geänderten/pausierten Auftrags werden nicht als neue gültige Prüfung übernommen. Ein Neustart behält gespeicherte Fälligkeiten bei. Nur ein Prozess pro Datenverzeichnis darf laufen.

## Discord

Webhook in Discord erstellen, dann `DEALCHECKER_DISCORD_WEBHOOK` in der Umgebung des gestarteten Prozesses setzen. Keine Webhook-Adresse in Git-Dateien ablegen. Danach in DealChecker Benachrichtigungen aktivieren. Es werden neue Ereignisse ab Aktivierung versendet; vorherige lokale Ereignisse werden nicht massenhaft nachgesendet.

Zustellfehler werden mit Retry-Status angezeigt. Ein Absturz exakt nach erfolgreichem Versand, aber vor der lokalen Bestätigung kann eine erneute Zustellung verursachen: Webhooks garantieren hier **mindestens einmal**, nicht exakt einmal. Der Preisalarm selbst wird dauerhaft dedupliziert. Es werden niemals automatische Käufe ausgelöst.

## Optionaler Windows-Autostart

Nach erfolgreichem manuellen Betrieb kann der Nutzer mit `Win+R` → `shell:startup` eine Verknüpfung zu `start.cmd` anlegen. Entfernen dieser Verknüpfung deaktiviert den Autostart. DealChecker verändert den Autostart nicht selbst. Die normale Konsolenversion bleibt bewusst sichtbar; für einen unbeaufsichtigten Betrieb kann später eine Aufgabenplaner-Konfiguration ergänzt werden.

## Fehlersuche

- Oberfläche nicht erreichbar: Konsolenfenster prüfen; [lokale Adresse](http://127.0.0.1:8765) verwenden. Einen anderen Port mit `python -m dealchecker --port 8766` wählen.
- „Läuft bereits“: vorhandene Instanz beenden. Die Betriebssystemsperre wird bei Prozessende automatisch frei; keine laufende Datenbank löschen.
- Keine Angebote: Quellenabdeckung, Schreibweise und Filter prüfen. Apple findet keine physischen Handelswaren. Preislose Abo-/Arcade-Einträge sind nicht als Kaufangebote nutzbar.
- Fehler nach Update: kompletten Datenordner sichern; keine Schema-Version manuell ändern. Neuere Datenbanken werden von älterer Software abgewiesen.
- Ein alter Preis bleibt sichtbar: Er ist eine vergangene Beobachtung, keine Zusage eines aktuellen Händlerpreises. Alter und Fehlerstatus beachten, vor Kauf im Shop prüfen.
