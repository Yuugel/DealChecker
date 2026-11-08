# Phase 3: Einkaufs- und Regressionstests

Geprüft am 8. November 2026 unter Windows, Python 3.14.6. Live-Abnahme gegen 18:02 Uhr MEZ mit dem normalen guarded HTTP-Client, ohne Anmeldung, Umgehung oder bezahlte Datenzugänge. Eigener Testdatenordner, kein Zugriff auf bestehende Benutzerdaten und kein echter Discord-Versand.

## Reale Kaufbeispiele

Für A, B und C wurden ALTERNATE, BerryBase und Bestenvergleich parallel über die lokale API durchsucht. Apple (Apps) und Modula24 (Möbelteile) sind für diese Kaufwünsche nicht relevant. Lidl hat keine Suchfunktion; dort wurde ein bekannter Produktlink geprüft. Alle folgenden Preisangaben sind Momentaufnahmen.

| Fall | Tatsächliche Suche / Produkt | Monitoring und Alarm |
|---|---|---|
| A: Nicer Dicer Fusion Smart, 16-teilig | Namenssuche: keine passenden Treffer. [Lidl-Direktlink](https://www.lidl.de/p/genius-nicer-dicer-nicer-dicer-fusion-smart-16-teilig-1-25-l-auffangbehaelter-klingen-aus-edelstahl/p100227834): Artikel `100227834`, EAN `4023012338110`, **43,99 € + 5,95 € = 49,94 €**, nicht lieferbar. Shop Lidl, Verkäufermetadaten fehlen. | Link aufgelöst, exakten Auftrag angelegt, Cache erneut geprüft, Anwendung neu gestartet und Produkt erneut abgerufen: zwei echte Beobachtungen, **kein Kaufpreisalarm**. Ein aktuell bestellbares Angebot dieses exakten Sets fehlt. |
| B: 8BitDo Ultimate 2C | Zwei BerryBase-Treffer: Bluetooth/Switch-kompatibel, Blau `RET00498` / EAN `6922621506355` und [Pink `RET00499`](https://www.berrybase.de/8bitdo-ultimate-2c-bluetooth-controller-switch-kompatibel-hall-effekt-joysticks-6-achsen-pink) / EAN `6922621506362`. Je **29,90 € Artikelpreis**, Versand unbekannt, Quelle meldet begrenzte Verfügbarkeit. Verkäufer BerryBase. | Pink als exakten Auftrag übernommen; nach Neustart zweite reale Beobachtung. **Kein Gesamtpreisalarm** bei unbekanntem Versand. Die Windows-/Android-Wireless-Ausführung wurde damit nicht bestätigt. |
| C: Waschmaschine ca. 8 kg, Budget 400 € | Budgetsuche ergab zwei ALTERNATE-Angebote. [Daewoo WM-FB8514W1A-DE](https://www.alternate.de/Daewoo/WM-FB8514W1A-DE-Waschmaschine/html/product/100166901): Artikel `100166901`, EAN `8698902514965`, **334,00 € + 59,99 € = 393,99 €**, weiß, lieferbar. Beschreibung und technische Daten bestätigen **8 kg**. Standardlieferung frei Bordsteinkante. | Auswahltreffer/Link übernommen, Wunschpreis 400 €. **Genau ein lokales Ereignis „Wunschpreis erreicht“**, auch nach Cacheprüfung und Neustart weiterhin eines; zwei reale Preisbeobachtungen. |

Der zweite Waschmaschinentreffer war Bomann WA 7196 (`100126852`) zu 337,99 € inklusive Versand, jedoch **9 kg** laut Produktbeschreibung. Die Anwendung besitzt keinen allgemeinen Kapazitätsfilter; die Auswahl des 8-kg-Modells wurde anhand der Händlerdaten geprüft. Ein BerryBase-Detailabruf zur breiten Waschmaschinensuche lieferte HTTP 404. Der Fehler blieb sichtbar; ALTERNATE wurde erfolgreich gespeichert. Bestenvergleich lieferte in der budgetbegrenzten C-Abnahme keine zulässigen Gesamtpreisangebote.

Zusätzliche Live-Proben: ALTERNATE ASUS RTX 5070 DUAL OC (`100132495`, EAN `4711636046213`, MPN `90YV0M17-M0NA00`) per Name, EAN und MPN gefunden und gezielt geprüft, 919,00 € + 0 € gemeldeter DE-Versand. Logitech MK235 (`1272686`): 22,99 € + 7,99 €. Lidl Parkside PBS 750 A1 (`100397572`): 41,99 € + 5,95 €, lieferbar. Mehrdeutige Lidl-Variantenlinks werden abgewiesen. [Quellen-/Zugriffsgrenzen](phase3-sources.md).

**Nicht live bewiesen:** tatsächlicher Preisrückgang während dieser kurzen Beobachtung, derselbe Artikel gleichzeitig bei mehreren integrierten Händlern mit vollständigem Gesamtpreis, Zustellung an einen echten Discord-Webhook, flächendeckende Suchvollständigkeit. Dafür werden keine synthetischen Treffer als Ersatz präsentiert.

## Automatisierte Regressionen, ausschließlich synthetisch

Vollständige Suite: **245 bestanden**. `compileall`, `pip check`, `node --check dealchecker/static/app.js` und `git diff --check` erfolgreich. Eine bestehende Starlette/httpx-Deprecation-Warnung bleibt; kein Testfehler. CI führt die Suite zusätzlich unter Windows/Ubuntu mit Python 3.11/3.14 aus; maßgeblich sind die Checks des jeweiligen PR-Commits.

Abgesichert: HTTPS/DNS/Redirect-/Body-/Headergrenzen; robots, Crawl-delay und Cache; Name/EAN/Modellsuche; Zubehör und widersprüchliche Varianten; Mitglieder-/Mengenpreise und Gültigkeitszeiträume; unbekannter Versand/Verkäufer; Referenzbudget und getrennte Verkäuferfilter; Quelle-Ausfälle und Backoff; mehrstündige Pause; Migration von Schema 6 auf 8 unter Erhalt alter Intervalle; Neustarts, Preisabfall, persistente Historie und unterdrückte Doppelalarme. Ein neuer Integrationstest führt synthetisches Händler-HTML durch den echten ALTERNATE-Parser, API, Engine und SQLite: eine Quelle fällt dauerhaft aus, die funktionierende Quelle speichert nach Neustart einen niedrigeren Preis und genau einen Alarm.

Webhook-Konfiguration: Speichern/Neustart/Entfernen, keine automatische Aktivierung, keine Geheimnisse in API- oder Validierungsantworten, geschützter lokaler Zugriff, ungültige Konfiguration beeinträchtigt Preisüberwachung nicht, Pfadnormalisierung kann das Webhook-Ziel nicht verändern, Versand prüft die Konfiguration nach Erwerb der gemeinsamen Sperre erneut. Ein dauerhafter Einweg-Fingerabdruck bindet die Versandwarteschlange an ihr Ziel: Entfernen/Wechseln des Webhooks verwirft alte ausstehende Zustellungen, auch bei Wechsel über die Prozessumgebung oder Neustart; gleicher Kanal behält Retries. Lokale Ereignisse bleiben erhalten. Sämtliche Webhooks in Tests sind synthetisch; kein externer Nachrichtenversand.

## Windows und Browser

Die vorhandene Windows-Installation startet mit `python -m dealchecker --port 8766` als eigener Prozess auf Loopback; verwendeter isolierter Testdatenordner: `artifacts/phase3-probes/acceptance-data`. Neustart und persistente Daten wurden geprüft. Der Testdienst wird nach Abschluss beendet, kein Autostart geändert.

Im echten Browser geprüft: Produktlink → Vorschau mit Identität/Preis/Versand → exakten Auftrag speichern → bearbeiten und erhaltene EAN/Verkäuferfilter/Wunschpreis prüfen; Namenssuche wählt physische Suchquellen vor und sperrt Lidl als reine Linkquelle; MediaMarkt-Link zeigt die konkrete fehlende Unterstützung; ungültiger Webhook wird ohne Konfiguration abgewiesen; Diagramm/Tabelle zeigen zwei reale Waschmaschinen-Beobachtungen. Keine Browser-Konsolenfehler. Rohproben und Bildschirmnachweis liegen lokal unter `artifacts/phase3-probes`, bleiben wegen Laufzeitdaten außerhalb von Git.

Zum Wiederholen: in einem neuen Datenordner die drei Suchbegriffe mit den genannten Quellen prüfen, die verlinkten Produkte auswählen, C auf 400 € Wunschpreis setzen, Verlauf ansehen, erneut prüfen und die Anwendung neu starten. Händlerpreise/Bestand dürfen sich seit diesem Bericht verändert haben; Erfolg bedeutet korrekte aktuelle Auswertung, nicht identische Preise.

## Nächste praktische Schritte

1. Artikelspezifische DE-Versandregeln für BerryBase belastbar bestätigen, damit Controller-Wunschpreisalarme möglich werden.
2. Weitere erlaubte öffentliche Händlerzugänge anhand desselben vollständigen Such-/Link-/Alarmablaufs ergänzen; reale gleiche EAN über mehrere Händler abnehmen.
3. Für große geschlossene Plattformen zuerst einen passenden privaten Zugang bestätigen: eBay-Produktion oder ggf. Keepa nach gesonderter Kostenfreigabe. Awin/Tradedoubler nicht als vorausgesetzte Standardlösung behandeln.

Dieser Stand erweitert die praktische Händlerabdeckung, ist aber **keine vollständige Produktfertigstellung oder flächendeckende Schnäppchensuche**.
