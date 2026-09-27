# Glossar

Alle Begriffe, die in Terminal, CSV und Interface auftauchen.

---

## Der Ablauf

**Themengebiet** (`subject`) — Ein Aufgabenbereich, den du vorgibst:
`dienstplan`, `kassenbuch`, `zeiterfassung`. Steht in `data/domains.txt`.
Alles wird nach Gebieten gruppiert.

**Muster** (`pattern`) — Eine Schablone mit `{d}` als Platzhalter:
`{d} vorlage`, `alternative zu {d}`. Steht in `data/patterns.txt`.

**Kandidat** — Ein erzeugtes Keyword, bevor irgendetwas darueber bekannt ist.
40 Gebiete x 34 Muster = 1.360 Kandidaten.

**Ueberlebender** — Ein Kandidat, der die drei harten Filter passiert hat.
Von 1.360 bleiben typisch 70.

**Layer 0 / 1 / 2** — Die drei Stufen der Pipeline. Layer 0 erzeugt
Kandidaten (kostenlos), Layer 1 holt Volumen und CPC (0,06 $ je 1000),
Layer 2 holt die SERP (0,0006 $ je Keyword). Layer 2 laeuft nur auf
Ueberlebende -- deshalb ist das Ganze billig.

**Seed** — Ein Startwort fuer die Autocomplete-Suche (`--seed`), die
Alternative zum Generator.

---

## Kennzahlen

**VOL** (`volume`) — Suchvolumen pro Monat, exakt aus Google Ads, nicht
geschaetzt. Filter: unter 300 raus (zu duenn), ueber 10.000 raus (dort
sitzt schon jemand mit Budget).

**CPC** — Was ein Werbeklick auf dieses Keyword kostet, in Euro. Das
staerkste Signal im Tool: ein hoher Klickpreis beweist, dass jemand an
diesen Suchenden Geld verdient. Unter 0,50 EUR fliegt raus -- dort wird
informiert, nicht verkauft.

**DIFF** (`difficulty`) — Wie besetzt die SERP ist, 0 bis 100. Niedrig
heisst angreifbar. Berechnet, nicht gekauft:

    DIFF = 100 * ( 0,50 * Anteil echter Produktseiten
                 + 0,35 * Anteil Platzhirsch-Domains
                 + 0,15 * (1 - Domain-Vielfalt) )

Nur SICHER eingestufte Treffer gehen ein. Unbekannte zaehlen NICHT als
Produktseite -- sonst wuerde jede unbekannte Domain die Difficulty
hochtreiben und die Chance verstecken.

**±** (`difficulty_pm`, `spread`) — Die Unschaerfe. `21±7` heisst: eine
einzige andere Einstufung unter den Top 10 verschoebe den Wert um 7
Punkte. Ergibt sich als 50 geteilt durch die Zahl sicherer Treffer. Wo die
Unschaerfe den Wert uebersteigt (`8±10`), bedeutet die Rangposition nichts.

**SICH** (`confidence_sure`) — Wie viele der Top-10-Treffer sicher
eingestuft wurden, z. B. `7/9`. Je hoeher, desto belastbarer die
Difficulty. Median liegt bei 7.

**TR** (`trend`) — Letzte 3 Monate gegen die 3 davor. Unter 1,0 heisst
weniger Nachfrage. ALLEIN NICHT LESBAR -- siehe SAI.

**SAI** (`saisonalitaet`) — Spitze zu Median ueber 12 Monate. Hoch heisst
stark saisonal. Erst beide zusammen sind lesbar: Trend 0,7 bei Saison 1,2
ist ein echter Rueckgang, Trend 0,1 bei Saison 4,0 ist Sommerpause.

**competition** — Googles eigener Wettbewerbsindex fuer Anzeigen, 0 bis
100. Sagt etwas ueber den Anzeigenmarkt, nichts ueber organisches Ranking.

**VAR** — Wie viele fast gleiche Keywords hinter dieser Zeile
zusammengefasst sind. `+4` heisst, es gibt vier Varianten desselben
Gebiets. `--flat` zeigt sie einzeln.

---

## Stufen (T)

Die Absicht hinter dem Keyword, erkannt am Muster. Deterministisch,
nachlesbar in `keyworld/intent.py`. Kein Modell.

| T | Name | Erkannt an | Bedeutung |
|---|------|-----------|-----------|
| 1 | Werkzeugnot | `in excel`, `vorlage`, `muster`, `automatisieren`, `konvertieren`, `exportieren` | Erledigt die Aufgabe gerade von Hand mit dem falschen Werkzeug. Staerkstes Produktsignal. |
| 2 | Kaufbereit | `alternative`, `preise`, `kosten`, `abo`, `app`, `software`, `tool`, `digital` | Will zahlen: sucht Ersatz, vergleicht Preise, oder sucht ausdruecklich ein Werkzeug. |
| 3 | Pflicht | `pflicht`, `vorgeschrieben`, `nachweis erstellen`, `frist`, `dokumentationspflicht` | Externer Zwang. Kauft nicht aus Lust, sondern weil er muss. |
| 4 | Orientierung | `beste X`, `wie funktioniert`, `was ist`, `vergleich`, `test` | Vergleicht oder lernt. Weitester Weg zum Umsatz -- sinkt nach unten. |
| 0 | Offen | kein Muster getroffen | Unbekannt, nicht schwach. Bleibt sichtbar und sinkt NICHT auf Stufe-4-Niveau. |

Wer vergleicht, kauft noch nicht: `beste dienstplan app` enthaelt das
Stufe-2-Wort `app`, ist aber Stufe 4.

---

## SERP-Marker

Zeigt Google auf dieses Keyword ueberhaupt Produktseiten?

| Marker | Anteil Produktseiten | Bedeutung |
|--------|---------------------|-----------|
| `PROD` | ab 40 % | Google rankt dort echte Produktseiten. Ein Produkt kann ranken. |
| `MIX` | 20-40 % | Gemischt. |
| `RED` | unter 20 % | Rein redaktionell. **Die wichtigste Warnung im Tool.** |
| `?` | zu wenig Daten | Unter 3 sicheren Treffern. Wird ZUERST gezeigt, nicht aussortiert. |

Eine niedrige Difficulty auf einer `RED`-SERP ist kein Einstieg, sondern
die falsche Suchabsicht: dort will Google keine Produktseite, egal wie
schwach die Konkurrenz aussieht.

`--serp PROD` zeigt nur die Zeilen, auf denen ein Produkt ranken kann.

---

## Signale

Was die SERP verraet. Gilt fuer die oberen 5 Plaetze. Die Zahl dahinter
ist die Anzahl.

| Signal | Bedeutung |
|--------|-----------|
| `FORUM` | Forum, Reddit oder StackOverflow in den Top 5. Der Markt hat keine gute Antwort. |
| `ARTIKEL3` | 3 Blogartikel statt Produktseiten. |
| `VORLAGE2` | 2 Vorlagen-Downloads ranken. Der Markt will eine Vorlage, und wer rankt, verschenkt sie. |
| `AMT5` | 5 Behoerden-, Kammer- oder Verbandsseiten. Ranken auf Autoritaet, verkaufen aber nichts. |
| `ALT` | Mindestens ein Treffer aelter als 3 Jahre. |
| `PORTAL` | Vergleichsportal (Capterra, OMR). Niemand besitzt das Keyword. |
| `NUR-EN` | Nur englischsprachige Treffer auf eine deutsche Anfrage. |
| `UNKLAR3` | 3 Treffer nicht sicher einstufbar. Difficulty steht auf duennerem Eis. |

---

## Kategorien je Treffer

Wie eine einzelne Seite in den Top 10 eingestuft wird.

**Besetzt** ist ein Platz nur von `product`. Alles andere haelt den Platz,
verkauft aber nichts, und ist von einem Produkt angreifbar.

| Kategorie | Erkannt an |
|-----------|-----------|
| `product` | `/preise` `/pricing` `/software` `/cloud`, Pfad mit `-app`, Preistabelle pro Monat, "kostenlos testen", Navigation zu Preisen, SoftwareApplication oder Product als Structured Data |
| `article` | Article/BlogPosting, Autoren-Markup, Datum in der URL, `/blog` `/magazin` `/ratgeber` |
| `forum` | DiscussionForumPosting, Thread-Markup, Antwortzaehler; Reddit, Gutefrage, YouTube, Pinterest |
| `download` | Downloaddatei (.xlsx, .pdf) oder Vorlagenseite mit Dateilink |
| `institution` | Kammern, Verbaende, Berufsgenossenschaften, Krankenkassen, Behoerden, Hochschulen |
| `marketplace` | Amazon, App Store, Play Store, Canva |
| `aggregator` | Capterra, OMR, Vergleichsportale |
| `unknown` | Keine ausreichenden Signale. **Wird NIE als Produktseite geraten.** |

**Confidence** — Wie sicher die Einstufung ist, 0 bis 1. Reinheit (wie
widerspruchsfrei) mal Beleglage (wie viel ueberhaupt). Unter 0,50 wird
nichts bewertet, sondern zur Pruefung gelegt.

    nur ein Datum          0,48   -> Pruefliste
    nur Produkt-CTA        0,61
    Structured Data        0,74
    CTA + Preisangabe      0,87
    Widerspruch 3 zu 3     0,37   -> Pruefliste

---

## Platzhirsch

Eine Domain, die in mindestens 25 % der SERPs EINES THEMENGEBIETS
auftaucht. Wer ueberall sitzt, ist teurer als zehn Einzelanbieter.
Geht mit 0,35 in die Difficulty ein.

Wichtig: je Gebiet, nicht je Lauf. Ueber 40 unzusammenhaengende Gebiete
erreicht keine Domain die Schwelle.

**TOP-DOMAIN** — Der erste Treffer der SERP, bevorzugt ein Platzhirsch.

---

## Deine Spalten

Diese beiden fasst das Tool nie an, auch nicht beim Aktualisieren der
Messwerte.

**Zustand** (`state`)

| Zustand | Bedeutung |
|---------|-----------|
| `new` | Noch nicht angesehen. |
| `shortlisted` | Du willst das genauer pruefen. |
| `reviewed` | Angesehen, Entscheidung offen. |
| `rejected` | Abgelehnt. Wird in kuenftigen Laeufen uebersprungen (`--include-rejected` holt sie zurueck). |

**Notiz** (`note`) — Dein Freitext.

---

## Pruefliste und Overrides

**Pruefliste** (`<lauf>-pruefliste.csv`) — Domains, deren Einstufung
unsicher blieb, nach Haeufigkeit sortiert. Die obersten tragen am meisten:
bei 120 unsicheren Domains decken die Top 20 rund 43 % aller betroffenen
Treffer ab.

**Overrides** (`data/overrides.csv`) — Deine Korrekturen. Format
`domain,type,note`. Schlaegt jede Heuristik UND den Seiten-Cache, wirkt
also ab dem naechsten Lauf sofort. Direkt editierbar oder ueber das
Interface.

---

## Geld

**Ledger** (`cost_ledger`) — Jeder API-Call mit dem von der API
**gemeldeten** Ist-Wert, nie einer Schaetzung.

**Gespart** — Was Cache-Treffer gekostet haetten. Wird mit 0 gebucht und
separat gefuehrt.

**Budget** (`KEYWORLD_RUN_BUDGET_USD`) — Hartes Limit je Lauf. Greift
VOR dem Call, nicht danach.

**Trockenlauf** — `scout` ohne `--go`. Zeigt nur die geschaetzten Kosten,
ruft nichts ab. Der Cache ist eingerechnet.

**Queue vs. Live** — Standard ist die Queue (0,06 $ je Task, ein paar
Minuten). `--live` ist schneller und gut dreimal teurer. Ein bezahlter
Queue-Task geht nie verloren: `keyworld fetch` sammelt ihn spaeter
kostenlos ein.

---

## Tiefenanalyse

**Umfang** — Wie viel der Dive einkauft. `schnell` nutzt nur bezahlte
Daten (0 $), `standard` holt das echte Keyword-Umfeld (0,12 $), `tief`
zusaetzlich die fehlenden SERPs (0,14 $).

**Entdoppelt** — Google meldet fuer `fusspflege`, `fußpflege`, `fusspflege app` und
`app fuer fusspflege` identische Zahlen: es ist dieselbe Suche. Addiert ergaebe
das eine doppelt so grosse Marktgroesse. Vor jeder Markteinschaetzung werden
Umlaut- und Wortstellungsvarianten deshalb zusammengefasst (bei einem Gebiet
11 % weniger, bei `stundenplan` 6 %).

**CPC volumengewichtet** — Der CPC des Feldes, gewichtet nach Suchvolumen.
Ein Keyword mit 20 EUR und 10 Suchen darf einen Markt nicht schoenrechnen,
in dem 10.000 Suchen bei 0,30 EUR liegen. Das ist die Zahl, die sagt, ob
im Feld ueberhaupt Geld liegt.

**Einordnung** — Ab welchem gewichteten CPC ein Markt was ist:

    unter 0,50 EUR   Informationssuche, kein Markt
    unter 1,50 EUR   schwach monetarisiert
    unter 3,00 EUR   Verbrauchermarkt
    unter 8,00 EUR   kleiner B2B-Markt
    darueber         harter B2B-Markt

**Verhaeltnis** (Saison) — Spitze zu Tal ueber 12 Monate. 7,5x heisst, der
staerkste Monat hat das 7,5-fache des schwaechsten.

**Wer die Plaetze haelt** — Die Kategorien aller Treffer im ganzen Feld,
nicht nur einer SERP. Zeigt auf einen Blick, ob dort Produkte, Ratgeber
oder Gratis-Vorlagen sitzen.

---

## Kommandos

| Kommando | Zweck | Kosten |
|----------|-------|--------|
| `probe` | Verbindung und Guthaben pruefen | 0 |
| `gen` | Gebiete x Muster anzeigen | 0 |
| `discover` | Autocomplete-Kandidaten | 0 |
| `scout` | Der eigentliche Lauf | ab 0,06 $ |
| `review` | Zustand und Notiz je Keyword | 0 |
| `queue` | Overrides anzeigen und ergaenzen | 0 |
| `compare` | Gebiete nebeneinander, aus dem Cache | 0 |
| `dive` | Tiefenanalyse eines Gebiets | 0 bis 0,14 $ |
| `rescore` | Urteile aus Rohsignalen neu ableiten | 0 |
| `serve` | Interface im Browser | 0 |
| `costs` | Ausgabenuebersicht | 0 |
| `fetch` | Bezahlte Queue-Tasks einsammeln | 0 |

Geld geben nur `scout --go` und `dive --go` aus. Alles andere ist
kostenlos.
