# Weboberfläche

Die lokale Oberfläche wird von `/` ausgeliefert und verwendet ausschließlich
statisches HTML, CSS und JavaScript. Sie benötigt keine zusätzlichen Pakete,
externen Bilder oder CDN-Ressourcen. Die Ansichten lesen den gebündelten
`GET /api/state`-Endpunkt und aktualisieren ihn alle zehn Sekunden. Änderungen
am offenen Watch-Formular werden bei diesen Aktualisierungen nicht überschrieben.

## Darstellung und Bedienung

- Das Dashboard zeigt aktive und pausierte Beobachtungen, aktuelle Quelltreffer,
  Ereignisse und konfigurierte Quellen. Die Standardabdeckung ist ausdrücklich
  Apple App Store Deutschland für Apps und Spiele; der Hinweis schließt
  In-App-Käufe und Abos aus. Weitere Quellen werden anhand des API-Katalogs
  dargestellt.
- Angebotskarten zeigen Artikelpreis, Versandstatus, bekannten Gesamtpreis,
  Verfügbarkeit, Quellenname, echten Prüfzeitpunkt und einen API-seitigen
  Veraltet-Status. Ein unbekannter Versand wird als unbekannt angezeigt und nie
  zum Artikelpreis addiert. Ein etwaiger Vergleich ist als beobachteter
  Vergleich gekennzeichnet.
- Das Watch-Formular enthält die vollständigen `WatchInput`-Felder. Preiseingaben
  werden vor dem API-Aufruf als sichere Ganzzahl-Cents serialisiert. Beim Ändern
  wird das vollständige Formular erneut übertragen. Quell-IDs, die nicht mehr im
  aktuellen Quellenkatalog stehen, bleiben als bestehende Auswahl erhalten,
  damit eine Bearbeitung sie nicht stillschweigend löscht. Die Oberfläche
  begrenzt Synonyme auf fünf und verlangt genau eine Quelle, wenn eine Produkt-ID
  oder Produkt-URL eingetragen wird.
- Die Verlaufstabelle zeigt gespeicherte Beobachtungen. Das SVG-Diagramm
  zeichnet nur Datensätze mit bekanntem Gesamtpreis; bei weniger als zwei
  geeigneten Beobachtungen erklärt es, warum kein Verlauf gezeichnet wird.
- Aktivität, Prüfprotokoll, Quellenstatus und Einstellungen stammen aus dem
  Backend. Benachrichtigungen sind bei fehlender Kanalkonfiguration als nicht
  verfügbar gekennzeichnet. Ereignisse zeigen den vom Backend gemeldeten
  Benachrichtigungsstatus, Versuche und Fehler. BF-Modus und Scheduler-Pause
  werden über den Einstellungsendpunkt geändert.

## Sicherheit und Zugänglichkeit

API-Texte werden als DOM-Text eingefügt; die Oberfläche verwendet kein
`innerHTML`. Angebotslinks werden vor Verwendung auf `http:` oder `https:` ohne
Zugangsdaten geprüft und öffnen mit `noopener noreferrer`. Änderungen verwenden
JSON-Anfragen. Native Dialoge, Formularbeschriftungen, sichtbare Tastaturfoki,
`aria-live`-Statusbereiche und Textalternativen für SVGs unterstützen
Tastaturbedienung und Screenreader. Bewegungsanimationen respektieren
`prefers-reduced-motion`.

## Prüfumfang und Grenzen

`node --check dealchecker/static/app.js` wurde ausgeführt. Ein Browser-
End-to-End-Lauf ist Teil der Integration durch den Parent. Die Oberfläche
interpretiert keine Angebotsdaten neu: Vergleich, Bewertung, Veraltung,
Konfiguration und Filterwirkung bleiben Aufgabe der API. Ein fehlender oder
unerreichbarer Backend-Dienst erzeugt einen sichtbaren Verbindungsstatus; es
werden keine Beispielangebote oder synthetischen Preise gezeigt.
