"""Absichtsstufen, abgeleitet aus dem Keyword-Muster selbst.

Kein Modell, keine API -- reine Mustererkennung, damit die Einstufung
deterministisch und nachvollziehbar bleibt. Wer eine Stufe nicht versteht,
kann sie hier nachlesen statt einem Score zu vertrauen.

  1  Werkzeugnot     Erledigt die Aufgabe gerade manuell mit dem falschen
                     Werkzeug. Staerkstes Produktsignal ueberhaupt.
  2  Wechsel         Sucht Ersatz fuer etwas Bestehendes. Zahlungsbereitschaft
                     ist bereits bewiesen.
  3  Kaufabsicht     Will kaufen, aber noch ohne konkrete Not.
  4  Orientierung    Vergleicht oder lernt. Weitester Weg zum Umsatz.
"""
import re

LADDER = {
    1: ("Werkzeugnot", [
        r" in excel", r" in google sheets", r" in sheets", r" in notion",
        r" in word", r" in airtable", r"excel ", r"spreadsheet",
        r"automatisier", r"automation", r"\bskript\b", r"\bscript\b",
        r"\bmakro\b", r"\bformel\b", r"workaround", r"\bohne ",
        r"selber (machen|bauen|erstellen|schreiben)", r"selbst (erstellen|bauen)",
        r"per hand", r"\bmanuell", r"zu viel zeit", r"\bnervt\b",
    ]),
    2: ("Wechsel", [
        r"alternative", r"\bersatz\b", r"\bstatt\b", r"\bvs\b", r"\bvs\.",
        r"umstieg", r"migration", r"migrieren", r"wechseln von", r"weg von",
        r"\bvorlage", r"\btemplate", r"\bmuster\b", r"\bboilerplate",
        r"\bexport(ieren)? (nach|zu)", r"\bimport(ieren)? (aus|von)",
    ]),
    3: ("Kaufabsicht", [
        r"\bkaufen\b", r"\bpreis", r"\bkosten\b", r"\bsoftware\b", r"\btool\b",
        r"\banbieter\b", r"\bdienstleister\b", r"\babo\b", r"\blizenz",
        r"\bangebot", r"online bestellen", r"\bbuchen\b", r"\bmieten\b",
        r"\bagentur\b", r"\bkurs\b", r"\bpaket\b",
    ]),
    4: ("Orientierung", [
        r"\bbeste[rsn]?\b", r"\btop \d+", r"wie funktioniert", r"\bwas ist\b",
        r"\bwas sind\b", r"\bwarum\b", r"\bwann\b", r"\bwie (kann|macht|viel)",
        r"\btest\b", r"testsieger", r"\berfahrungen\b", r"\bvergleich",
        r"\bbewertung", r"\breview", r"\bempfehlung", r"\banleitung\b",
        r"\btutorial\b", r"\bbedeutung\b", r"\bdefinition\b", r"\blernen\b",
    ]),
}

DEFAULT_LEVEL = 3   # blosse Nominalphrase in kommerziellem Umfeld

_COMPILED = {lvl: (name, [re.compile(p) for p in pats])
             for lvl, (name, pats) in LADDER.items()}


def classify(keyword):
    """-> (stufe, name, ausloesendes_muster). Stufe 1 gewinnt vor 2 vor 3 vor 4."""
    padded = " %s " % keyword.lower().strip()
    for lvl in (1, 2, 3, 4):
        name, pats = _COMPILED[lvl]
        for p in pats:
            if p.search(padded):
                return lvl, name, p.pattern
    return DEFAULT_LEVEL, LADDER[DEFAULT_LEVEL][0], "-"


def label(level):
    return LADDER.get(level, ("?", []))[0]
