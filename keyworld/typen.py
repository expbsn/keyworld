"""Produkttyp je Keyword: was man bauen wuerde, um diese Suche zu bedienen.

Unabhaengig von der Absichtsstufe. Die Stufe sagt, wie dringend jemand
sucht; der Typ sagt, welche Art Produkt die Antwort waere. Erst Regeln aus
den Suchwoertern, was die Regeln nicht treffen, bleibt "unklar" -- bis das
Sprachmodell es einordnet (gespeichert in kw_typ, nie still geraten).
"""
import re

# id, Anzeigename, Beschreibung, Muster
TYPEN = [
    ("software", "App und Software", "Suchen nach einem Programm oder einer App",
     r"\bapp\b|software|programm|\btool|online|digital|alternative|preise"),
    ("vorlage", "Vorlage und Dokument", "Suchen nach einem fertigen Dokument",
     r"vorlage|muster|\bexcel\b|\bword\b|\bpdf\b|formular"),
    ("rechner", "Rechner und Generator", "Suchen nach etwas, das ein Ergebnis erzeugt",
     r"rechner|berechnen|generator|erstellen|kalkul"),
    ("dienst", "Dienstleistung", "Suchen nach jemandem, der es erledigt",
     r"lassen\b|agentur|beratung|service\b|dienstleist"),
    ("wissen", "Wissen", "Reine Info-Suchen, selten ein Produkt",
     r"\bwas ist\b|\bwie\b|\bfrist\b|\bdefinition\b|\bgesetz"),
]
UNKLAR = ("unklar", "Noch nicht eingeordnet", "Die Regeln greifen nicht")
IDS = [t[0] for t in TYPEN]

# Reihenfolge bei Mehrdeutigkeit: "angebot erstellen vorlage" ist eine
# Vorlage, "was ist eine app" ist Wissen.
_VORRANG = ["wissen", "vorlage", "dienst", "rechner", "software"]
_MUSTER = dict((t[0], re.compile(t[3])) for t in TYPEN)


def typ_aus_regeln(keyword):
    kw = (keyword or "").lower()
    for tid in _VORRANG:
        if _MUSTER[tid].search(kw):
            return tid
    return UNKLAR[0]


SCHEMA = """
CREATE TABLE IF NOT EXISTS kw_typ (
    keyword  TEXT NOT NULL,
    market   TEXT NOT NULL,
    typ      TEXT NOT NULL,
    quelle   TEXT NOT NULL,
    ts       REAL NOT NULL,
    PRIMARY KEY (keyword, market)
);
"""


def gespeichert(conn, market):
    conn.executescript(SCHEMA)
    return dict((r["keyword"], r["typ"]) for r in conn.execute(
        "SELECT keyword, typ FROM kw_typ WHERE market=?", (market,)))


def typ(keyword, gespeicherte=None):
    """Regeln zuerst. Eine gespeicherte Einordnung zaehlt nur fuer das, was
    die Regeln offen lassen -- so kann eine bessere Regel sie nie ueberstimmen
    muessen und umgekehrt."""
    t = typ_aus_regeln(keyword)
    if t == UNKLAR[0] and gespeicherte:
        return gespeicherte.get(keyword.lower(), t)
    return t


def speichern(conn, market, zuordnung, quelle, ts):
    conn.executescript(SCHEMA)
    for kw, t in zuordnung.items():
        if t in IDS:
            conn.execute("INSERT OR REPLACE INTO kw_typ VALUES (?,?,?,?,?)",
                         (kw.lower(), market, t, quelle, ts))
    conn.commit()
