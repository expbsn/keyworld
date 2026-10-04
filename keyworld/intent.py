"""Absichtsstufen aus dem Keyword-Muster. Deterministisch, kein Modell.

  1  Werkzeugnot    Erledigt die Aufgabe gerade von Hand mit dem falschen
                    Werkzeug. Staerkstes Produktsignal.
  2  Frust          Bestehende Loesung ist zu teuer, kaputt oder wird gekuendigt.
                    Zahlungsbereitschaft bereits bewiesen.
  3  Pflicht        Externer Zwang: Gesetz, Frist, Nachweis. Kauft nicht aus
                    Lust, sondern weil er muss.
  4  Orientierung   Vergleicht oder lernt. Weitester Weg zum Umsatz.
  0  Offen          Kein Muster getroffen. Wird NICHT wie Stufe 4 behandelt --
                    Fehlerbias: im Zweifel zeigen, nicht versenken.
"""
import re

LADDER = {
    1: ("Werkzeugnot", [
        r"\bin excel\b", r"\bin google sheets\b", r"\bin sheets\b", r"\bin word\b",
        r"excel[- ]vorlage", r"excel vorlage", r"\bexcel\b",
        r"automatisier", r"automatisch (erstellen|generieren|ausfuellen|befuellen)",
        r"konvertier", r"\bumwandeln\b", r"exportier", r"extrahier",
        r"stapelweise", r"\bbatch\b", r"massenhaft", r"\bmakro\b", r"\bformel\b",
        r"selber (machen|bauen|erstellen|schreiben)", r"per hand", r"\bmanuell",
        r"\bvorlage\b", r"\btemplate\b", r"\bmuster\b", r"\bformular\b",
    ]),
    2: ("Kaufbereit", [
        r"alternative", r"zu teuer", r"\bkuendig", r"\bkündig",
        r"funktioniert nicht", r"geht nicht", r"\bproblem",
        r"g(ue|ü)nstige[rs]? alternative", r"\bersatz\b", r"\bstatt\b",
        r"\bvs\b", r"\bvs\.", r"weg von", r"umstieg", r"migration",
        r"\bnervt\b", r"\blahm\b", r"\bzu kompliziert\b",
        # Preisrecherche zu einem konkreten Produkt ist Wechselabsicht.
        # Wortgrenzen sind hier entscheidend: "nebenkostenabrechnung" und
        # "kostenlos" duerfen NICHT feuern.
        r"\bpreise?\b", r"\bkosten\b", r"\bkostet\b", r"\babo\b",
        r"\blizenz", r"\btarif", r"was kostet",
        # Ausdrueckliche Werkzeugsuche. Wer "dienstplan app" sucht, weiss
        # bereits, dass er Software will -- Nachfrage ist bewiesen, nur der
        # Anbieter fehlt noch.
        r"\bapp\b", r"\bsoftware\b", r"\bprogramm\b", r"\btool\b",
        r"\bdigital\b", r"\bonline\b", r"\bcloud\b",
    ]),
    3: ("Pflicht", [
        r"pflicht", r"vorgeschrieben", r"verpflichtend", r"gesetzlich",
        r"nachweis (erstellen|fuehren|führen)", r"dokumentationspflicht",
        r"\bfrist\b", r"\bfristen\b", r"\bvorschrift", r"\bverordnung",
        r"\bgesetz\b", r"\bdsgvo\b", r"\bgobd\b", r"\bnachweispflicht",
        r"\bmeldepflicht", r"\baufbewahrungs",
        # Technische Normen und Regelwerke. Wer danach sucht, muss sich
        # danach richten -- das ist Pflicht, auch ohne das Wort "Pflicht".
        r"\bvob\b", r"\bdin \d", r"\bdin\b", r"\bhoai\b", r"\bvde\b",
        r"\bdguv\b", r"\buvv\b", r"\bbgv\b", r"\bisо?\s?\d{4}",
        r"\bnorm(gerecht|konform)?\b", r"\bregelwerk", r"\brichtlinie",
        r"\bpruefbar", r"\bprüfbar", r"\brevisionssicher",
    ]),
    4: ("Orientierung", [
        r"\bbeste[rsn]?\b.*\bapp\b", r"\bbeste[rsn]?\b", r"\btop \d+",
        r"wie funktioniert", r"\bwas ist\b", r"\bwas sind\b", r"\bwarum\b",
        r"\bwie (kann|macht|viel|lange)", r"\bvergleich", r"\btest\b",
        r"testsieger", r"\berfahrungen\b", r"\breview", r"\banleitung\b",
        r"\btutorial\b", r"\bbedeutung\b", r"\bdefinition\b", r"\berklaert\b",
    ]),
}

OPEN_LEVEL = 0
NAMES = dict((lvl, name) for lvl, (name, _) in LADDER.items())
NAMES[OPEN_LEVEL] = "Offen"

# Sortierrang: 1-3 zuerst, dann Offen, Stufe 4 zuletzt. Offen sinkt bewusst
# NICHT auf das Niveau von Stufe 4 -- ein unerkanntes Muster ist keine
# schwache Absicht, nur eine unbekannte.
RANK = {1: 0, 2: 1, 3: 2, OPEN_LEVEL: 3, 4: 4}

# Wer vergleicht, kauft noch nicht. "beste dienstplan app" enthaelt zwar das
# Stufe-2-Wort "app", ist aber Orientierung -- diese Marker schlagen Stufe 2.
ORIENTATION_GUARD = [
    r"\bbeste[rsn]?\b", r"\bwelche[rsn]?\b", r"\btop \d+", r"\bvergleich",
    r"wie funktioniert", r"\bwas ist\b", r"\bwas sind\b", r"\bwarum\b",
    r"\bwie (kann|macht|viel|lange)", r"\btest\b", r"testsieger",
    r"\berfahrungen\b", r"\breview", r"\bempfehlung",
]

# Reihenfolge der Pruefung, nicht Reihenfolge der Stufen: die spezifischsten
# Muster zuerst, damit ein generisches Wort kein spezifisches ueberstimmt.
PRECEDENCE = (1, 3, "guard", 2, 4)

_COMPILED = dict((lvl, (name, [re.compile(p) for p in pats]))
                 for lvl, (name, pats) in LADDER.items())
_GUARD = [re.compile(p) for p in ORIENTATION_GUARD]


def classify(keyword):
    """-> (stufe, name, ausloesendes_muster)."""
    padded = " %s " % keyword.lower().strip()
    for step in PRECEDENCE:
        if step == "guard":
            for p in _GUARD:
                if p.search(padded):
                    return 4, NAMES[4], p.pattern
            continue
        name, pats = _COMPILED[step]
        for p in pats:
            if p.search(padded):
                return step, name, p.pattern
    return OPEN_LEVEL, NAMES[OPEN_LEVEL], "-"


def label(level):
    return NAMES.get(level, "?")


def is_visible(level):
    """Stufe 1-3 und Offen bleiben sichtbar, Stufe 4 sinkt."""
    return level != 4
