# Keyworld

Keyword-Research zum Scouten von Nischen fuer ein eigenes Produkt.
Rein funktional, Mono-Output, transparente Kosten.

Nicht gebaut fuer Content-Traffic. Gebaut fuer die Frage: verdient hier
jemand Geld, und komme ich rein?

## Pipeline

Vier Stufen. Teure Calls laufen nur auf das, was die Filter ueberlebt hat.

    LAYER 0  DISCOVERY                                    0 $
      Google / Amazon / YouTube Autocomplete
      Seed -> 200-2.000 Kandidaten
                    v
    FILTER   hart, feste Reihenfolge, kein Score          0 $
      1  Volumen  < 300     raus   zu duenn fuer ein Produkt
      2  Volumen  > 10.000  raus   dort sitzt schon jemand mit Budget
      3  CPC      < 0,50    raus   Informationssuche, kein Geld im Markt
                    v
    LAYER 1  METRIKEN                      0,06 $ / 1000 KW
      DataForSEO Google Ads Search Volume
      -> exaktes Volumen, CPC, Competition, 12-Monats-Historie
                    v
    LAYER 2  SERP                        0,0006 $ / Keyword
      Top 10 je Ueberlebendem
      -> Difficulty, Platzhirsche, Suchabsicht der SERP
                    v
      Sortierung: Stufe 1-2 zuerst, darin Difficulty aufsteigend

Ein kompletter Nischenlauf kostet rund **0,07 $**.

## Die zwei Achsen

**CPC filtert.** Ein hoher Klickpreis beweist, dass jemand an diesen
Suchenden Geld verdient. Volumen ist nur Mindestvoraussetzung, keine
Rangfolge -- 5.000 Suchen ohne Kaufabsicht sind schlechter als 400 mit.

**Difficulty ranked.** Und zwar eine selbst berechnete, nicht die gekaufte.

Der gekaufte Keyword-Difficulty-Wert von DataForSEO ist bei Long-Tails zu
59 % leer (Rueckgabe 0 = "keine SERP-Daten", nicht "trivial"), und genau dort
liegen die Nischen. Stattdessen wird aus der Zusammensetzung der Top 10
gerechnet:

    difficulty = 100 * ( 0,50 * Anteil echter Produktseiten
                       + 0,35 * Anteil Platzhirsch-Domains
                       + 0,15 * (1 - Domain-Vielfalt) )

Niedrig heisst angreifbar. Ein Ratgeber-Artikel, ein Forenthread oder eine
Amazon-Suchergebnisseite sind von einer echten Produktseite angreifbar --
eine echte Produktseite ist es nicht. Platzhirsche sind Domains, die in
mindestens 25 % aller SERPs des Laufs auftauchen; wer ueberall sitzt, ist
teurer als zehn Einzelanbieter.

Domain-Vielfalt allein misst nichts: zehn verschiedene Domains koennen zehn
etablierte Hersteller sein.

## Absichtsstufen

Aus dem Keyword-Muster, deterministisch, nachlesbar in `intent.py`.
Kein Modell.

    1  Werkzeugnot   "in excel", "automatisieren", "ohne", "selber machen"
                     Erledigt die Aufgabe gerade manuell mit dem falschen
                     Werkzeug. Staerkstes Produktsignal ueberhaupt.
    2  Wechsel       "alternative zu", "vorlage", "statt", "vs"
                     Sucht Ersatz. Zahlungsbereitschaft ist bewiesen.
    3  Kaufabsicht   "kaufen", "preis", "software", "anbieter"
    4  Orientierung  "beste app", "wie funktioniert", "test", "vergleich"

Stufe 1-2 steht im eigenen Block oben, auch bei mittelmaessigem CPC.
Innerhalb jedes Blocks gibt es genau eine Sortierachse.

## SERP-Marker

    PROD   Google zeigt echte Produktseiten -- ein Produkt kann dort ranken
    MIX    gemischt
    RED    rein redaktionell

`RED` ist die wichtigste Warnung im Tool. Eine niedrige Difficulty auf einer
redaktionellen SERP ist kein Einstieg, sondern die falsche Suchabsicht.
Beispiel: `hundefutter tests` hat die niedrigste Difficulty im Set (16),
weil 7 von 10 Treffern Ratgeber sind -- eine Produktseite rankt dort nie.
Mit `--product-serp-only` fliegen diese raus.

## Setup

    cp .env.example .env     # DataForSEO Login + API-Passwort eintragen
    ./keyworld.sh probe      # Verbindung und Guthaben pruefen, kostenlos

Das API-Passwort ist nicht das Login-Passwort -- es steht unter
app.dataforseo.com/api-access. Layer 0 laeuft ohne Credentials.

Setze `KEYWORLD_RUN_BUDGET_USD` unter deinen Kontostand. Der Guard greift
vor dem Call, nicht danach.

## Nutzung

    ./keyworld.sh discover "zeiterfassung"           # kostenlos, nur Kandidaten
    ./keyworld.sh scout "zeiterfassung"              # Trockenlauf mit Kostenschaetzung
    ./keyworld.sh scout "zeiterfassung" --go         # kompletter Lauf
    ./keyworld.sh scout "a" "b" "c" --go             # mehrere Seeds in einem Lauf
    ./keyworld.sh scout --seeds-file seeds.txt --go  # Seeds aus Datei
    ./keyworld.sh costs                              # Ausgabenuebersicht
    ./keyworld.sh costs --run <ID>                   # Detail pro Run
    ./keyworld.sh fetch                              # offene Queue-Tasks einsammeln

Ohne `--go` passiert nichts Kostenpflichtiges.

Wichtige Schalter:

    --report FILE          HTML-Report, eine Datei, sortierbar
    --harvest FILE         verwandte Suchen und PAA als Seed-Datei
    --product-serp-only    redaktionelle SERPs aussortieren
    --min-cpc 1.00         Filter nachziehen
    --max-serp 120         Deckel fuer Layer 2, nimmt die hoechsten CPCs
    --live                 schneller, gut 3x teurer
    --deep                 zusaetzlich a-z Suffixe in Layer 0

## Der Kreislauf

Jede SERP-Antwort enthaelt "Aehnliche Suchanfragen" und "Nutzer fragen auch"
-- bereits bezahlt, sonst ungenutzt. `--harvest` schreibt sie als Seed-Datei
fuer den naechsten Lauf:

    ./keyworld.sh scout "zeiterfassung" --go --harvest next.txt
    ./keyworld.sh scout --seeds-file next.txt --go --report runde2.html

Ein Lauf ueber "zeiterfassung" liefert so 132 neue Kandidaten zum Nulltarif.

## Kosten

| Keywords | Layer 1 | Layer 2 (Ueberlebende) | Summe |
|---------:|--------:|-----------------------:|------:|
|      258 |   $0.06 |      34 -> $0.020      | $0.08 |
|    1.000 |   $0.06 |     120 -> $0.072      | $0.13 |
|   10.000 |   $0.60 |     500 -> $0.300      | $0.90 |

Ein Layer-1-Task deckt bis zu 1000 Keywords ab -- ein Call fuer 1 Keyword
kostet genauso viel wie einer fuer 1000. Der Client buendelt immer maximal.

Der SQLite-Cache (30 Tage fuer Metriken, 14 fuer SERPs) macht
Wiederholungslaeufe praktisch gratis. Jeder Call landet im `cost_ledger` mit
dem von der API **gemeldeten** Ist-Wert, nie mit einer Schaetzung;
Cache-Treffer werden mit 0 gebucht und separat als Ersparnis gefuehrt.

`--queued` (Standard) postet asynchron und pollt; `task_get` ist kostenlos.
Jeder Task wird vor dem Pollen registriert -- bricht der Abruf ab, sammelt
`keyworld fetch` ihn spaeter ein, ohne erneut zu zahlen.

## Aufbau

    keyworld/
      config.py            .env, Markt-Defaults
      store.py             SQLite: Cache, Kosten-Ledger, Budget-Guard, Queue
      intent.py            Absichtsleiter aus Keyword-Mustern
      filters.py           Filterkette mit Abgangsprotokoll, Sortierung
      serp.py              Ergebnistyp-Klassifikation, Difficulty, Platzhirsche
      render.py            Mono-Tabellen fuers Terminal
      report.py            HTML-Report, eine Datei, keine Abhaengigkeiten
      cli.py               Kommandos
      providers/
        base.py            KeywordMetrics, Provider-Vertraege
        free.py            Layer 0: Autocomplete
        dataforseo.py      Layer 1/2: Volume, CPC, SERP, Difficulty

Metrik-Provider sind hinter `MetricsProvider` austauschbar.

## Grenzen

Die Ergebnistyp-Klassifikation arbeitet mit Domain-, Pfad- und Titelmustern.
Sie erkennt Foren, Vergleichsportale, Marktplaetze und Verlage zuverlaessig;
ein Blog auf einer unbekannten Domain ohne Pfadmarker kann als Produktseite
durchgehen. Die Listen stehen oben in `serp.py` und sind erweiterbar.

Difficulty misst SERP-Zusammensetzung, keine Backlink-Staerke. Das ist
Absicht -- Backlink-Daten kosten ein Vielfaches und beantworten die Frage
"wer haelt die Plaetze" schlechter als die Plaetze selbst.
