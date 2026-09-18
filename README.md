# Keyworld

Keyword-Research-Tool zum Scouten von Business Opportunities.
Rein funktional, Mono-Output, transparente Kosten.

## Datenquellen-Entscheidung

Dreistufige Kaskade. Teure Calls nur fuer Kandidaten, die es verdient haben.

    LAYER 0  DISCOVERY                                  0 $
      Google / Amazon / YouTube Autocomplete
      Seed -> 200-2.000 Kandidaten, echte Nutzerformulierungen
                  v  Dedup + Normalisierung
    LAYER 1  METRIKEN                       0,06 $ / 1000 KW
      DataForSEO Google Ads Search Volume (Bulk)
      -> exaktes Volumen, CPC, Competition, 12-Monats-Historie
                  v  Score-Filter: Top 1-5 %
    LAYER 2  VALIDIERUNG                 ~0,002 $ / Keyword   [geplant]
      DataForSEO SERP fuer die Finalisten
      -> Difficulty, Intent, Ads-Praesenz

Warum DataForSEO und nicht die kostenlose Google Ads API: ohne aktives
Ad-Budget liefert der Keyword Planner nur Bucket-Ranges ("10K-100K") statt
Zahlen, und der Basic-Access-Token braucht ein Approval. DataForSEO zieht
ueber eigene Ads-Accounts und liefert exakte Werte ab Minute eins.

Warum die kostenlose Layer 0 trotzdem: Autosuggest enthaelt Long-Tail, das in
Keyword-Datenbanken oft fehlt -- etwa "alternative zu <wettbewerber>", ein
Wechsel-Intent mit hoher kommerzieller Relevanz. Kostet nur Zeit.

## Kosten

| Keywords | Tasks | Queue ($0.06) | Live ($0.09) |
|---------:|------:|--------------:|-------------:|
|      258 |     1 |         $0.06 |        $0.09 |
|    1.000 |     1 |         $0.06 |        $0.09 |
|   10.000 |    10 |         $0.60 |        $0.90 |
|  100.000 |   100 |         $6.00 |        $9.00 |

Ein Task deckt bis zu 1000 Keywords ab -- ein Call fuer 1 Keyword kostet
genauso viel wie einer fuer 1000. Der Client buendelt deshalb immer maximal.

Der SQLite-Cache (TTL 30 Tage) macht Wiederholungs-Runs praktisch gratis;
Suchvolumina aendern sich monatlich, nicht taeglich.

## Setup

    cp .env.example .env     # DataForSEO Login/Passwort eintragen
    ./keyworld.sh probe      # Verbindung + Guthaben pruefen (kostenlos)

Layer 0 laeuft ohne Credentials.

## Nutzung

    ./keyworld.sh discover "hundefutter"          # kostenlos, nur Kandidaten
    ./keyworld.sh discover "hundefutter" --deep   # zusaetzlich a-z Suffixe
    ./keyworld.sh scout "hundefutter"             # Trockenlauf: nur Kostenschaetzung
    ./keyworld.sh scout "hundefutter" --go        # wirklich abrufen
    ./keyworld.sh scout "hundefutter" --go --queued --budget 0.50
    ./keyworld.sh fetch                           # offene Queue-Tasks einsammeln
    ./keyworld.sh costs                           # Ausgabenuebersicht
    ./keyworld.sh costs --run <ID>                # Detail pro Run

`scout` ohne `--go` ist immer ein Trockenlauf und gibt nur die geschaetzten
Kosten aus. Jeder Run hat ausserdem ein hartes Budget-Limit
(`KEYWORLD_RUN_BUDGET_USD`), das vor dem Call greift, nicht danach.

## Live vs. Queue

`--queued` postet den Task asynchron ($0.06) und pollt ihn ab; `task_get` ist
kostenlos. Jeder gepostete Task wird in `pending_tasks` registriert, bevor
gepollt wird. Bricht der Abruf ab, geht das Geld nicht verloren -- der Task
laesst sich spaeter mit `keyworld fetch` kostenlos einsammeln.

Ohne `--queued` laeuft der Live-Modus ($0.09), Antwort in Sekunden.

## Kostentransparenz

Jeder API-Call landet im `cost_ledger` mit dem von der API **gemeldeten**
Ist-Wert, nie mit einer Schaetzung. Cache-Treffer werden mit 0 gebucht und
separat als Ersparnis gefuehrt.

## Aufbau

    keyworld/
      config.py            .env-Laden, Markt-Defaults
      store.py             SQLite: Cache + Kosten-Ledger + Budget-Guard
      render.py            Mono-Tabellen fuers Terminal
      cli.py               Kommandos
      providers/
        base.py            KeywordMetrics, Provider-Vertraege
        free.py            Layer 0: Autocomplete (Google/Amazon/YouTube)
        dataforseo.py      Layer 1/2: Volume, CPC, Keyword Ideas

Provider sind hinter `MetricsProvider` austauschbar -- Keywords Everywhere
oder die Google Ads API lassen sich ohne Aenderung an der Pipeline
einstecken.

## Noch offen

- Layer 2: SERP-Abruf und Difficulty
- Opportunity-Scoring (Volumen x CPC x Trend / Difficulty)
- HTML-Report mit Charts
