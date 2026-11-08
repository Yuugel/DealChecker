# Bestenvergleich: Suche und Preisreferenzen

Stand: 8. November 2026. Die [offizielle API-Dokumentation](https://bestenvergleich.de/entwickler) erlaubt kostenlose Abfragen ohne Schlüssel und nennt Apps als Einsatzfall. Quellenverlinkung ist erforderlich; ein Weiterverkauf unbearbeiteter Daten ist nicht erlaubt. Der Betreiber ist im [Impressum](https://bestenvergleich.de/impressum) ausgewiesen. DealChecker verlinkt jede Referenz auf deren Produktseite. Dort können Affiliate-Links enthalten sein.

## Tatsächlich implementiert

- Dynamische Suche über `/api/v1/search?q=…&limit=25`, höchstens sechs Begriffe einschließlich Synonymen. Bis zu 25 Treffer je Begriff, kein Anspruch auf vollständige Markt- oder Katalogabdeckung.
- Gezielte Abfrage über `/api/v1/products/{id}`, EAN oder dokumentierten Kategorie/Produktpfad. EAN-Prüfziffer und zurückgelieferte Identität werden geprüft. Nicht belegbare Varianten-/Modellfelder werden abgewiesen.
- EUR-Artikelreferenz, Produkt-ID, Name, EAN, Hersteller und Kategorie, soweit vorhanden. Cache für 20 Minuten, höchstens 128 Einträge; Cachetreffer behalten ihren ursprünglichen Beobachtungszeitpunkt.
- Dokumentiertes Limit: 120 Anfragen/Minute. Der gemeinsame HTTP-Client nutzt einen konservativen Hostabstand von 3,2 Sekunden. Die API nennt einen Aktualisierungszyklus von 20 Minuten; dies ist keine von uns bestätigte Aktualitätsgarantie der Händlerpreise.

## Grenzen

Bei den Live-Proben war trotz `offer_count=5` die Detail-Liste `offers=[]`. Der API-Mindestpreis belegt daher **keinen bestimmten Händler, Verkäufer, Versandpreis oder lieferbaren Gesamtpreis**. DealChecker speichert ihn als `offer_type=reference`, mit unbekanntem Versand und Bestand. Es entstehen keine Kaufpreisalarme und keine Einträge im belastbaren Händlervergleich. Auch historische API-Daten werden nicht als eigene Beobachtungen importiert.

Die Preisobergrenze filtert nach Gesamtpreis. Wer Referenzen entdecken möchte, lässt sie leer; ein unbekannter Gesamtpreis erfüllt diese Grenze nicht. Der Wunschpreis erzeugt für Referenzen keinen Alarm. Zur Kaufentscheidung muss die verlinkte Quelle und anschließend das tatsächliche Händlerangebot geprüft werden.

## Echte Live-Proben

Am 8. November 2026 lieferte der tatsächliche Adapter 23 bepreiste Referenzen für „Airfryer“ und 14 für „Bosch“. „Samsung“ lieferte in dieser Stichprobe keine nutzbaren Treffer. Die gezielte Abfrage von Produkt `9184068` bestätigte **GOURMETmaxx Airfryer**, EAN `4067522012115`, Artikelreferenz **39,99 €**. Das ist ausdrücklich kein verifizierter Kauf-Gesamtpreis. Bosch-Treffer enthielten Backofen-/Kochfeldkombinationen. Weitere Nachweise stehen im [Phase-2-Prüfbericht](phase2-verification.md).
