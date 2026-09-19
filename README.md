# Keyworld

Nischen-Scouting fuer ein eigenes kleines Softwareprodukt.
Kein Content-SEO. Kein Traffic-Selbstzweck.

Das Tool verwandelt einen grossen Keyword-Raum in eine KURZE Shortlist, die
manuelle Pruefung verdient. Es ist ein Filter, kein Orakel -- das Urteil
ueber eine SERP faellst du.

## Fehlerbias

Falsch Aussortiertes ist teuer, zu viel Gezeigtes ist billig. Ein
uebersehenes Keyword kostet die ganze Nische, fuenf zusaetzlich gepruefte
SERPs kosten zehn Minuten. Im Zweifel zeigt das Tool. Konkret:

- Eine unbekannte Domain wird NIE als Produktseite geraten. Sie heisst
  `unknown` und landet in der Pruefliste.
- Bleiben weniger als 3 sicher eingestufte Treffer, ist die Difficulty
  `?` -- das Keyword wird dann ZUERST gezeigt, nicht aussortiert.
- Ein Keyword, dessen Muster keiner Stufe entspricht, ist Stufe `0 Offen`
  und bleibt sichtbar. Es sinkt nicht auf das Niveau von Stufe 4.

## Pipeline

    GENERATOR                                             0 $
      Themengebiete x Muster, kartesisch
      8 Gebiete x 24 Muster = 192 Keywords
                    v
    FILTER   hart, feste Reihenfolge, kein Score          0 $
      1  Volumen  < 300     raus
      2  Volumen  > 10.000  raus
      3  CPC      < 0,50    raus
                    v
    LAYER 1  METRIKEN                      0,06 $ / 1000 KW
      Volumen, CPC, Competition, 12-Monats-Historie
                    v
    LAYER 2  SERP                        0,0006 $ / Keyword
      Top 10 je Ueberlebendem
                    v
    KLASSIFIKATION                                        0 $
      Seiteninhalte der Treffer, nebenlaeufig
                    v
      Sortierung: Difficulty aufsteigend, unbekannte zuerst

Ein Layer-1-Task deckt bis zu 1000 Keywords ab -- fuer dieselben 0,06 $
kannst du statt 3 Themengebieten auch 40 durchlaufen lassen. Generiere
breit, die Filter raeumen auf.

## Keine Mischnote

Difficulty ist die einzige Sortierachse. Volumen, CPC und Difficulty werden
nicht zu einem Score verrechnet -- Marktgroesse und SEO-Luecke laufen
gegenlaeufig, und genau diese Spannung soll sichtbar bleiben.

    difficulty = 100 * ( 0,50 * Anteil Produktseiten unter den SICHEREN Treffern
                       + 0,35 * Anteil Platzhirsch-Domains
                       + 0,15 * (1 - Domain-Vielfalt) )

Unsichere und unbekannte Treffer zaehlen NICHT als Produktseite. Sonst
wuerde jede unbekannte Domain die Difficulty hochtreiben und die Chance
verstecken.

## Absichtsstufen

Aus dem Keyword-Muster, deterministisch, nachlesbar in `intent.py`.
Erste Spalte, nicht Teil eines Scores.

    1  Werkzeugnot   "in excel", "vorlage", "muster", "automatisieren",
                     "konvertieren", "exportieren", "extrahieren"
    2  Kaufbereit    "alternative", "zu teuer", "preise", "kosten", "abo",
                     "app", "software", "tool", "digital", "online"
                     Will zahlen: sucht Ersatz, vergleicht Preise, oder
                     sucht ausdruecklich ein Werkzeug. "dienstplan app"
                     ist die direkteste Produktnachfrage, die es gibt.
    3  Pflicht       "pflicht 2026", "vorgeschrieben", "nachweis erstellen",
                     "dokumentationspflicht", "frist"
    4  Orientierung  "beste X app", "wie funktioniert", "was ist"   -> sinkt
    0  Offen         kein Muster getroffen                          -> sichtbar

## Zwei Listen, nicht eine

Gemessen ueber 1359 Keywords: Stufe-1-Muster brauchen AUFGABEN als
Themengebiet, Stufe-2-Muster brauchen PRODUKTNAMEN.

    data/domains.txt   Aufgaben    -> {d} vorlage, {d} in excel, {d} app
    data/brands.txt    Produkte    -> {d} alternative, {d} preise

"alternative zu kassenbuch" sucht niemand, "trello alternative" schon.
Marken sind fast dreimal so ergiebig (8,3 % ueber der Volumenschwelle
gegen 3,0 % bei Aufgaben).

Wortstellung entscheidet: `{d} alternative` liefert, `alternative zu {d}`
liefert nichts. Im Deutschen wird nachgestellt gesucht.

Tote Muster kosten nichts -- 120 und 1360 Keywords kosten beide 0,06 $
je Task. Es gibt also keinen Grund, ein Muster zu loeschen, nur einen
Grund, eines hinzuzufuegen.

## Schwaechesignale

Gelten fuer die oberen 5 Plaetze. Das sind die eigentlichen Chancen.

    FORUM      Forum, Reddit oder StackOverflow unter den Top 5
    ARTIKELn   n Blogartikel statt Produktseiten
    ALT        ein Treffer aelter als 3 Jahre
    NUR-EN     nur englischsprachige Treffer auf eine deutsche Anfrage
    VORLAGE    eine Tabellen- oder Vorlagendatei rankt
    PORTAL     Vergleichsportal
    UNKLARn    n Treffer nicht sicher einstufbar

Dazu der SERP-Marker: `PROD` Google zeigt Produktseiten, `MIX` gemischt,
`RED` rein redaktionell, `?` zu duenne Beleglage. `RED` ist die wichtigste
Warnung -- niedrige Difficulty auf redaktioneller SERP ist kein Einstieg,
sondern die falsche Suchabsicht.

## Klassifikation

Statt Domain-Listen werden die Seiten selbst gelesen, nebenlaeufig, mit
30-Tage-Cache. Kostet nichts ausser Zeit -- rund 450 Seiten in vier Minuten.
82 % werden sicher eingestuft.

Besetzt ist ein Platz nur von einer echten Produkt- oder Anbieterseite.
Alles andere haelt den Platz, verkauft aber nichts, und ist von einem
Produkt angreifbar.

    Produkt      /pricing /preise /plans /signup /software /cloud, Pfad mit
                 -app, Preistabelle pro Monat, "kostenlos testen",
                 Navigation zu Preisen oder Funktionen, Anbietersprache,
                 SoftwareApplication oder Product als Structured Data
    Artikel      Article/BlogPosting, Autoren-Markup, Datum in der URL,
                 /blog /magazin /ratgeber
    Forum        DiscussionForumPosting, Thread-Markup, Antwortzaehler
    Vorlage      Downloaddatei oder Vorlagenseite mit Dateilink. Fuer dieses
                 Tool die interessanteste Kategorie: der Markt will eine
                 Vorlage, und wer rankt, verschenkt sie
    Institution  Kammern, Verbaende, Berufsgenossenschaften, Krankenkassen,
                 Behoerden, Hochschulen. Ranken auf Autoritaet
    Marktplatz   Amazon, App Store, Play Store
    Aggregator   Capterra, OMR, Vergleichsportale

Ein Veroeffentlichungsdatum zaehlt NICHT als eigenstaendiger Artikelbeleg --
es steht als CMS-Artefakt auf zahllosen Produktseiten und drueckte sie
frueher unter die Schwelle.

Jedes Urteil traegt eine Confidence aus Reinheit (wie widerspruchsfrei) mal
Beleglage (wie viel ueberhaupt):

    nur ein Datum          0,48   -> Pruefliste
    nur Produkt-CTA        0,61
    Structured Data        0,74
    CTA + Preisangabe      0,87
    Widerspruch 3 zu 3     0,37   -> Pruefliste

Unter 0,50 wird nichts bewertet, sondern zur Pruefung gelegt.

## Die Difficulty sagt, wie genau sie ist

Jede Difficulty traegt eine Unschaerfe: `21±7` heisst, dass eine einzige
andere Einstufung unter den Top 10 den Wert um 7 Punkte verschoebe. Sie
ergibt sich aus der Zahl sicher eingestufter Treffer (50 geteilt durch sie).

Bei sieben sicheren Treffern sind das rund 7 Punkte. Zwei Keywords, die
naeher beieinander liegen, sind nicht unterscheidbar -- und wo die
Unschaerfe den Wert uebersteigt (`8±10`), bedeutet die Rangposition
nichts. Das gehoert in die Ausgabe, nicht in eine Fussnote.

## Tests

    ./.venv/bin/python -m unittest discover -s tests

21 Tests ueber die reinen Funktionen: Absichtsstufen samt Wortgrenzen,
Filterreihenfolge, Confidence-Kalibrierung, Difficulty bei duenner
Beleglage, Schwaechesignale, Verdichtung. Kein Netz, keine API, keine
Kosten.

## Korrekturen sind Daten, kein Code

`data/overrides.csv` wird bei jedem Lauf geladen und schlaegt jede
Heuristik. Direkt editierbar:

    domain,type,note
    sieda.com,product,Dienstplan-Software
    meinblog.de,article,

Oder ueber die CLI:

    ./keyworld.sh queue --domain sieda.com --type product --note "Anbieter"
    ./keyworld.sh queue                      # zeigt alle Overrides

Jeder Lauf schreibt zusaetzlich `<lauf>-pruefliste.csv` im Format von
overrides.csv -- Spalte `type` ausfuellen, Zeilen nach overrides.csv
kopieren, fertig.

Overrides schlagen auch den Seiten-Cache und werden nie hineingeschrieben.
Eine frische Korrektur wirkt sofort im naechsten Lauf, nicht erst nach
Ablauf der 30-Tage-Frist.

## Zustand je Keyword

Ergebnisse liegen in `data/keyworld.db` (SQLite, eine Datei, kein Server)
und ueberdauern Laeufe. Ein erneuter Lauf fragt nichts erneut ab, was schon
da ist, und zeigt nichts erneut, was du abgelehnt hast.

    ./keyworld.sh review "rechnung schreiben vorlage" --state shortlisted \
        --note "CPC 9 EUR, SERP nur MIX"
    ./keyworld.sh review --filter-state shortlisted
    ./keyworld.sh review                     # alle, mit Zustandszaehlung

Zustaende: `new` `shortlisted` `reviewed` `rejected`. Die Spalten `state`
und `note` gehoeren dir -- das Tool fasst sie nie an, auch nicht beim
Aktualisieren der Messwerte.

## Setup

    cp .env.example .env     # DataForSEO Login + API-Passwort
    ./keyworld.sh probe      # kostenlos

Das API-Passwort steht unter app.dataforseo.com/api-access und ist nicht
das Login-Passwort. Setze `KEYWORLD_RUN_BUDGET_USD` unter deinen Kontostand;
der Guard greift vor dem Call.

## Nutzung

    # Themengebiete sammeln
    cp data/domains.example.txt data/domains.txt   # anpassen

    # Vorschau, was generiert wuerde
    ./keyworld.sh gen --domains data/domains.txt

    # Trockenlauf mit Kostenschaetzung
    ./keyworld.sh scout --domains data/domains.txt

    # Kompletter Lauf
    ./keyworld.sh scout --domains data/domains.txt --go --budget 0.50

    # Einzelne Gebiete
    ./keyworld.sh scout --domain zeiterfassung --domain dienstplan --go

    # Autocomplete statt Generator
    ./keyworld.sh scout --seed zeiterfassung --go

Ohne `--go` passiert nichts Kostenpflichtiges. Jeder Lauf schreibt
`reports/<slug>.csv` und, bei unsicheren Domains,
`reports/<slug>-pruefliste.csv`.

Weitere Schalter:

    --patterns FILE        eigene Muster statt data/patterns.txt
    --keywords-file FILE   fertige Keywords, eine je Zeile
    --harvest FILE         verwandte Suchen und PAA fuer den naechsten Lauf
    --serp PROD            nur SERPs, auf denen Produktseiten ranken
    --max-serp 120         Deckel fuer Layer 2, nimmt die hoechsten CPCs
    --include-rejected     Abgelehnte doch wieder zeigen
    --workers 8            parallele Seitenabrufe
    --min-cpc 1.00         Filter nachziehen
    --report FILE          HTML, niedrigste Prioritaet

## Kosten

| Keywords | Layer 1 | Layer 2 (Ueberlebende) | Summe |
|---------:|--------:|-----------------------:|------:|
|       72 |   $0.06 |       4 -> $0.002      | $0.06 |
|      258 |   $0.06 |      34 -> $0.020      | $0.08 |
|    1.000 |   $0.06 |     120 -> $0.072      | $0.13 |

Die Seitenklassifikation kostet nichts. Jeder API-Call landet im
`cost_ledger` mit dem von der API **gemeldeten** Ist-Wert, nie mit einer
Schaetzung; Cache-Treffer werden mit 0 gebucht und als Ersparnis gefuehrt.

    ./keyworld.sh costs            # alle Laeufe
    ./keyworld.sh costs --run ID   # Detail
    ./keyworld.sh fetch            # bezahlte, nicht abgeholte Queue-Tasks

## Aufbau

    keyworld/
      config.py            .env, Markt-Defaults
      store.py             SQLite: Cache, Kosten-Ledger, Keyword-Zustand, Queue
      generate.py          kartesisches Produkt Gebiete x Muster
      intent.py            Absichtsstufen aus Keyword-Mustern
      filters.py           Filterkette mit Abgangsprotokoll, Sortierung
      classify.py          Seitenklassifikation, Confidence, Overrides
      serp.py              Schwaechesignale, Difficulty, Platzhirsche
      export.py            CSV
      render.py            Mono-Tabellen fuers Terminal
      report.py            HTML, niedrigste Prioritaet
      cli.py               Kommandos
      providers/           DataForSEO, Autocomplete
    data/
      patterns.txt         Muster, editierbar
      domains.example.txt  Themengebiete, Vorlage
      overrides.csv        deine Korrekturen
      keyworld.db          Cache, Kosten, Zustaende

## Interface

    ./keyworld.sh serve              # http://127.0.0.1:8765
    ./keyworld.sh serve --port 9000  # anderer Port
    pkill -f 'keyworld.cli serve'    # beenden

Ist der Port belegt, wird das erklaert statt geworfen: laeuft dort bereits
eine Keyworld-Instanz, oeffnet sich nur der Browser; ist es etwas anderes,
weicht der Server auf den naechsten freien Port aus.

Lokal, stdlib-HTTP, keine Abhaengigkeit, kein Framework. Duenne Schicht
ueber denselben Funktionen wie die CLI -- es gibt keine Logik, die nur dort
existiert. Drei Module:

    Shortlist   verdichtet je Gebiet, filterbar nach Zustand, Stufe und
                SERP-Absicht. Zustand und Notiz direkt setzen.
    Pruefliste  unsichere Domains nach Haeufigkeit, mit den Signalen, die
                zur Unsicherheit gefuehrt haben. Eine Auswahl schreibt eine
                Zeile in overrides.csv.
    Kosten      Ledger, Runs, Cache-Ersparnis.

Tastatur in der Shortlist:

    j k      bewegen
    s        shortlisted        r   rejected        v   reviewed
    n        Notiz              Enter   SERP in Google oeffnen

Das Interface scoutet NICHT. Abrufen kostet Geld, und Geld gibt man auf der
Kommandozeile aus, nicht per Klick im Browser. Es liest den Zustandsspeicher
und schreibt genau zwei Dinge zurueck: deinen Zustand je Keyword und deine
Korrekturen.

Die Pruefliste zeigt, wie weit die Arbeit traegt: bei 120 unsicheren Domains
decken die obersten 20 bereits 43 % aller betroffenen Treffer ab.

## Verdichtung und Trend

72 Zeilen sind in Wahrheit etwa 25 Chancen -- "dienstplan vorlage",
"dienstplan excel vorlage" und "dienstplan vorlage word" sind dieselbe
Gelegenheit. Der Scout verdichtet deshalb je Themengebiet und zeigt die
vertretbarste Zeile mit der Zahl der Varianten. `--flat` schaltet das ab.

Der Trend (letzte 3 Monate gegen die 3 davor) steht als Spalte `TR` und
laesst sich mit `--min-trend 0.8` filtern, ist aber standardmaessig AUS:
ueber nur 12 Monate Historie laesst sich Niedergang nicht von Saison
trennen. Ein Schulthema im August sieht aus wie eine sterbende Nische.

Daneben steht `SAI`, die Saisonalitaet (Spitze zu Median). Erst beide
zusammen sind lesbar: Trend 0,7 bei Saison 1,2 ist ein echter Rueckgang,
Trend 0,1 bei Saison 4,0 ist schlicht Sommerpause.

## Nicht-Ziele

Das Urteil ueber eine SERP wird nicht automatisiert. Ob die Top 3 schwache
Blogartikel oder starke Produktseiten sind, entscheidest du. Das Tool
liefert die Shortlist und den Ort, an dem dein Urteil landet.

Das Interface ist ein lokaler Prozess auf 127.0.0.1, kein Dashboard und
kein Dienst -- es laeuft, solange du es laufen laesst. Abgerufen werden nur
die Seiten, die fuer die Klassifikationssignale noetig sind: die Treffer der
SERPs, die du ohnehin bezahlt hast.
