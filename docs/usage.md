# Bedienung und Grenzen

## Überwachungen

„Produktlink auflösen“ erkennt unterstützte Händler und zeigt eine überprüfbare Vorschau. „Dieses Produkt beobachten“ übernimmt Quell-ID, Link, EAN, Modell, Variante und Hersteller. Normale Produktaufträge brauchen keine JSON-Dateien oder Terminalbefehle. Nicht unterstützte Händler und mehrdeutige Produktlinks erhalten eine sichtbare Erklärung. „Produktname suchen“ wählt eingerichtete physische Suchquellen vor; Apple bleibt für Apps wählbar, Lidl unterstützt nur Links.

Eine exakte Überwachung prüft ein identifiziertes Produkt. Eine dynamische Suche fragt die gewählten Quellen mit Suchbegriff und Synonymen erneut ab und kann dadurch neue Produkte entdecken. Mehrdeutige exakte Titel werden nicht stillschweigend einem ähnlichen Produkt zugeordnet. „Exakt beobachten“ an einem Treffer übernimmt dessen Quell-ID und URL in einen neuen Auftrag; die breite Suchanfrage wird dabei entfernt.

App-ID und Produkt-URL sind quellenspezifisch. EAN/GTIN, Modell und Variante können nur genutzt werden, wenn die Quelle sie liefert. Ein nicht unterstütztes Identitätsfeld führt zu einem sichtbaren Fehler. Quellenauswahl, Händlerfilter und Verkäuferfilter sind getrennt: Ein Feed kann mehrere Händler enthalten. Hersteller-, Kategorie- und Textfilter benötigen passende Quelldaten; eine unbekannte Angabe erfüllt den Filter nicht.

Die Preisobergrenze beschränkt Discovery-Ergebnisse. Der Wunschpreis steuert Alarme. Beide beziehen sich auf bekannte Gesamtpreise. Gutscheine und Grundpreise werden als Hinweise angezeigt; unbekannte Gutscheinbedingungen werden nicht automatisch vom Preis abgezogen. Unterschiedliche Packungsgrößen/Varianten werden nicht ohne belegte Identität miteinander verglichen.

## Produktidentität und Referenzen

Verkäufer, Variante, Packungsgröße und Zustand werden in der Angebotsidentität getrennt gespeichert. Ein aktueller Händlervergleich erscheint nur bei mindestens zwei verschiedenen Händlern, frischen lieferbaren Einzelangeboten und bekannten Gesamtpreisen. Gruppiert wird über eine prüfziffergültige GTIN oder vollständig belegte Hersteller-/Modell-/Varianten-/Packungsangaben. Unterschiede und fehlende Merkmale können bewusst getrennte Gruppen ergeben; ähnliche Namen allein reichen nie. Die Vergleichsgruppe verändert die historische Alarmbewertung nicht.

Bestenvergleich liefert **Preisreferenzen**, keine bestätigten Händlerangebote. Referenzen haben weder bekannten Versand noch belastbaren Bestand; ihr Verlauf zeigt ausdrücklich den Artikelreferenzpreis. Sie lösen keine Kaufpreisalarme aus und nehmen nicht am Gesamtpreisvergleich teil. Ein gesetztes Gesamtpreislimit filtert sie aus, weil der Gesamtpreis unbekannt ist. Mit dem separaten „Referenzbudget“ können Artikelreferenzen dennoch bis zu diesem Betrag angezeigt werden; sie erzeugen weiterhin keine Kaufalarme. [Quellenbeschreibung](bestenvergleich-source.md).

## Bewertung

Die Anwendung betrachtet ausschließlich die eigene, gespeicherte Beobachtungshistorie desselben Angebots, Händlers und derselben Variante. Der erste Treffer kann den Wunschpreis erreichen, aber noch keinen historischen Tiefstpreis belegen.

1. Bekannter Gesamtpreis und lieferbares Angebot: Wunschpreis prüfen.
2. Strikt niedriger als jede frühere geeignete Beobachtung: neuer beobachteter Bestpreis.
3. Mindestens drei vorherige geeignete Beobachtungen und mindestens 10 % unter deren Median: sehr gutes Angebot.
4. Mindestens 5 % Rückgang gegenüber der letzten geeigneten Beobachtung: interessanter Preisrückgang.

Vergleichspreise sind **beobachtete historische Preise**, kein repräsentativer Marktpreis. Der angezeigte Vergleich und die Ersparnis müssen nicht dem Bewertungsgrenzwert entsprechen: Sie beziehen sich auf den vorher beobachteten Tiefstpreis. Erst längere Beobachtungszeiträume liefern eine brauchbare Grundlage. Veraltete Daten, unbekannte Versandkosten oder unbekannte/nicht verfügbare Ware erzeugen keinen Kaufpreisalarm.

Nach einem Alarm wird dasselbe Angebot nur bei einer weiteren relevanten Verbesserung unter dem zuletzt gemeldeten Preis und der vorherigen geeigneten Beobachtung erneut gemeldet. Diese historische Vergleichsbeobachtung darf älter sein, etwa nach mehrstündiger Rechnerpause; die neue Preisbeobachtung muss weiterhin frisch sein. Unveränderte Preise, Preiserhöhungen und bloße Wiederkehr zum alten Preis erzeugen keine Doppelmeldungen. Ereignisse bleiben lokal verfügbar, auch wenn Discord nicht eingerichtet ist.

## Zeitplanung

| Überwachung | Normal | Black Week |
|---|---:|---:|
| Breite Suche, jede Priorität | 8 Stunden | 3 Stunden |
| Exakt · Kritisch | 30 Minuten | 5 Minuten |
| Exakt · Hoch | 45 Minuten | 10 Minuten |
| Exakt · Normal | 7 Stunden | 45 Minuten |
| Exakt · Niedrig | 8 Stunden | 1 Stunde |

Eigene Normal- und Black-Week-Intervalle zwischen 5 Minuten und 24 Stunden sind möglich. Quellenspezifische Paare haben Vorrang vor Auftragswerten; beide Werte angeben oder beide leer lassen. Das strengere Quellenminimum gilt immer: Modula24/Apple 5 Minuten, Bestenvergleich 20 Minuten, ALTERNATE/BerryBase/Lidl 30 Minuten. Strengere aktuelle robots-Crawl-delays gelten zusätzlich. Die Oberfläche zeigt die wirksamen Intervalle je Quelle.

Beim Upgrade behalten vorhandene Aufträge den vorherigen Normal-/Black-Friday-Takt als explizite Werte. Für die neuen automatischen Standards diese Felder leeren. Black Friday lässt sich für 24 Stunden oder 7 Tage aktivieren und endet automatisch. Beim Wechsel werden gesunde Quellen neu terminiert; bestehende Fehlerfristen werden nicht abgekürzt. Beispielwerte für später freigeschaltete Quellen wären Amazon 2 Stunden/20 Minuten und MediaMarkt/Saturn 7 Stunden/45 Minuten. Diese Beispiele bedeuten **keine vorhandenen Händleradapter**.

Cache, Anbieterlimits, parallele Aufträge und Fehler-Backoff können die tatsächliche Prüfung verzögern. „Jetzt prüfen“ fragt gesunde Quellen sofort an, verwendet jedoch weiterhin deren Cache und HTTP-Limits; fehlerhafte Quellen behalten ihre Wiederholungsfrist. Ein Cachetreffer erhält keinen künstlich neuen Beobachtungszeitpunkt. Bekannte Modula24-Varianten mit URL werden gezielt samt Versandangebot abgefragt; nur eine unbekannte reine Varianten-ID benötigt einmal den Katalog zur URL-Auflösung.

Fehler-Backoff gilt getrennt je Quelle und Überwachung. Fällt eine Quelle dauerhaft aus, verlängert sich nur ihre Wiederholungsfrist (bis zum 16-fachen Intervall); funktionierende Quellen behalten ihren normalen Takt. Die nächste Prüfung des Auftrags ist die früheste Quellenfälligkeit. Auch wenn eine fehlerhafte Quelle gerade auf ihren Wiederholungsversuch wartet, bleibt ihr Fehler sichtbar. Angebote einer noch nicht fälligen Quelle werden durch die Prüfung einer anderen Quelle nicht automatisch ungültig.

„Pause“ verhindert neue Prüfungen. Eine bereits laufende Anfrage kann noch beendet werden; Ergebnisse eines inzwischen geänderten/pausierten Auftrags werden nicht als neue gültige Prüfung übernommen. Ein Neustart behält gespeicherte Fälligkeiten bei. Nur ein Prozess pro Datenverzeichnis darf laufen.

## Discord

Webhook in Discord erstellen und unter **Einstellungen → Benachrichtigungen → Webhook-Adresse** eintragen. „Webhook lokal speichern“ sendet keine Testnachricht und aktiviert Meldungen nicht. Danach den Schalter aktivieren. Die Adresse wird nie wieder angezeigt; eine neue Eingabe ersetzt sie, „Lokale Konfiguration entfernen“ löscht sie und deaktiviert Meldungen. Alternativ setzt `DEALCHECKER_DISCORD_WEBHOOK` die Adresse über die Prozessumgebung; diese Konfiguration lässt sich in der Oberfläche nicht überschreiben. Fehlerhafte lokale Konfiguration stoppt die Preisüberwachung nicht, sondern deaktiviert den Versand und zeigt einen Fehler. Die Datei `notifications.json` im Datenordner ist nicht verschlüsselt; einen nur für den Benutzer zugänglichen Ordner verwenden und Backups schützen. Keine Webhook-Adresse in Git-Dateien ablegen. Es werden neue Ereignisse ab Aktivierung versendet; vorherige lokale Ereignisse werden nicht massenhaft nachgesendet.

Zustellfehler werden mit Retry-Status angezeigt. Beim Entfernen oder Wechseln des Kanals werden ausstehende Zustellungen für den alten Kanal verworfen; lokale Ereignisse bleiben erhalten. Das gilt auch für einen Umgebungswechsel beim Neustart. Beim ersten Upgrade auf die Kanalbindung (Schema 8) werden alte ausstehende Zustellungen ohne belegtes Ziel vorsichtshalber verworfen. Ein Absturz exakt nach erfolgreichem Versand, aber vor der lokalen Bestätigung kann eine erneute Zustellung verursachen: Webhooks garantieren hier **mindestens einmal**, nicht exakt einmal. Der Preisalarm selbst wird dauerhaft dedupliziert. Es werden niemals automatische Käufe ausgelöst.

## Optionaler Windows-Autostart

Nach erfolgreichem manuellen Betrieb kann der Nutzer mit `Win+R` → `shell:startup` eine Verknüpfung zu `start.cmd` anlegen. Entfernen dieser Verknüpfung deaktiviert den Autostart. DealChecker verändert den Autostart nicht selbst. Die normale Konsolenversion bleibt bewusst sichtbar; für einen unbeaufsichtigten Betrieb kann später eine Aufgabenplaner-Konfiguration ergänzt werden.

## Fehlersuche

- Oberfläche nicht erreichbar: Konsolenfenster prüfen; [lokale Adresse](http://127.0.0.1:8765) verwenden. Einen anderen Port mit `python -m dealchecker --port 8766` wählen.
- „Läuft bereits“: vorhandene Instanz beenden. Die Betriebssystemsperre wird bei Prozessende automatisch frei; keine laufende Datenbank löschen.
- Keine Angebote: Quellenabdeckung, Schreibweise und Filter prüfen. Apple findet keine physischen Handelswaren. Preislose Abo-/Arcade-Einträge sind nicht als Kaufangebote nutzbar.
- Fehler nach Update: kompletten Datenordner sichern; keine Schema-Version manuell ändern. Neuere Datenbanken werden von älterer Software abgewiesen.
- Ein alter Preis bleibt sichtbar: Er ist eine vergangene Beobachtung, keine Zusage eines aktuellen Händlerpreises. Alter und Fehlerstatus beachten, vor Kauf im Shop prüfen.
