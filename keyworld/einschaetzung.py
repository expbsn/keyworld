"""Einschaetzung und Dialog je Keyword.

Das Modell bekommt ausschliesslich, was Keyworld gemessen hat, und soll
nichts darueber hinaus behaupten. Der Dialog wird je Keyword gespeichert:
Nachfragen sehen die erste Einschaetzung und dieselben Zahlen.
"""
import json
from collections import Counter

from .classify import CONFIDENCE_FLOOR
from .dive import ANTWORTET, ist_luecke
from .intent import classify as classify_intent, label as tier_label
from .llm import LLMFehler, schaetzung, tokens_ungefaehr
from .store import now
from . import typen

SCHEMA = """
CREATE TABLE IF NOT EXISTS dialog (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword  TEXT NOT NULL,
    market   TEXT NOT NULL,
    ts       REAL NOT NULL,
    rolle    TEXT NOT NULL,
    art      TEXT NOT NULL,
    inhalt   TEXT NOT NULL,
    model    TEXT NOT NULL DEFAULT '',
    kosten   REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_dialog_kw ON dialog(keyword, market);
"""

ABSCHNITTE = ["Die Zahlen", "Seite 1", "Die Chance", "Das Risiko", "Nächster Schritt"]
ART_NAMEN = {"product": "Anbieter", "article": "Artikel", "forum": "Forum",
             "marketplace": "Marktplatz", "aggregator": "Portal", "download": "Vorlage zum Download",
             "institution": "Behörde oder Verband", "unknown": "unklar"}

SYSTEM = """Du berätst eine einzelne Entwicklerin oder einen einzelnen Entwickler, \
die genau ein kleines Softwareprodukt (App, Web-Tool oder Plugin) für den \
deutschsprachigen Markt bauen und verkaufen wollen. Du bekommst die Messwerte \
eines Keywords aus dem Recherche-Tool Keyworld und schätzt ein, ob sich hier \
ein Produkt lohnt.

Begriffe:
- Volumen: Suchen pro Monat in Deutschland (Google Ads).
- CPC: was Werbende pro Klick zahlen. Hoher CPC heißt, jemand verdient an diesen Suchern.
- Difficulty 0 bis 100: Anteil echter Anbieterseiten, wiederkehrender Platzhirsche und \
fehlender Vielfalt auf Seite 1. Freiraum ist 100 minus Difficulty. Die Streuung (±) sagt, \
wie stark eine einzige falsch eingestufte Seite den Wert verschiebt.
- Trend: Suchen der letzten drei Monate gegenüber den drei davor.
- Saison: höchster Monat geteilt durch den Median. Über 2 ist stark saisonal.
- Lücke: auf Seite 1 steht kein einziger sicher erkannter Anbieter, aber Foren, \
Artikel oder Behörden beantworten die Frage.

Regeln:
- Stütze dich nur auf die gelieferten Zahlen und Domains. Erfinde keine Wettbewerber, \
Preise, Nutzerzahlen oder Marktgrößen. Allgemeines Wissen über Branchen und Pflichten \
darfst du nutzen, kennzeichne es aber nicht als Messung.
- Nenne Unsicherheit offen: wenige sicher eingestufte Treffer, große Streuung, \
fehlende Monatsdaten.
- Schreibe Deutsch, duze, kurz und konkret. Keine Ausrufezeichen, keine \
Werbesprache. Zahlen im deutschen Format (1.300 Suchen, 2,91 €).
- Ein Urteil, das abrät, ist genauso wertvoll wie eins, das zurät."""

FORMAT = """Antworte als JSON in genau diesem Format:
{"urteil": "Ein Satz, höchstens 14 Wörter, ohne Punkt am Ende",
 "ton": 0.0 bis 1.0, wie vielversprechend für ein eigenes Produkt,
 "abschnitte": [
   {"titel": "Die Zahlen", "text": "2 bis 3 Sätze"},
   {"titel": "Seite 1", "text": "2 bis 3 Sätze"},
   {"titel": "Die Chance", "text": "2 bis 3 Sätze"},
   {"titel": "Das Risiko", "text": "1 bis 3 Sätze"},
   {"titel": "Nächster Schritt", "text": "1 bis 2 Sätze, konkret"}]}"""

DIALOG = """Beantworte jetzt Nachfragen zu diesem Keyword. Antworte in normalem \
Text, ohne Überschriften und ohne Markdown, höchstens 120 Wörter, wenn die Frage \
nicht ausdrücklich mehr verlangt."""


def _conn_schema(conn):
    conn.executescript(SCHEMA)


# -- Speicher -----------------------------------------------------------------
def verlauf(conn, market, keyword):
    _conn_schema(conn)
    out = []
    for r in conn.execute("SELECT * FROM dialog WHERE keyword=? AND market=? ORDER BY id",
                          (keyword.lower(), market)):
        inhalt = r["inhalt"]
        if r["art"] == "einschaetzung":
            try:
                inhalt = json.loads(inhalt)
            except ValueError:
                pass
        out.append({"rolle": r["rolle"], "art": r["art"], "inhalt": inhalt,
                    "model": r["model"], "kosten": r["kosten"], "ts": r["ts"]})
    return out


def themen(conn, market, limit=15):
    """Keywords mit gespeichertem Dialog, zuletzt besprochen zuerst."""
    _conn_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT keyword, MAX(ts) AS ts, COUNT(*) AS n FROM dialog WHERE market=?"
        " GROUP BY keyword ORDER BY ts DESC LIMIT ?", (market, limit))]


def _anhaengen(conn, market, keyword, rolle, art, inhalt, model="", kosten=0.0):
    _conn_schema(conn)
    conn.execute("INSERT INTO dialog (keyword, market, ts, rolle, art, inhalt, model, kosten)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 (keyword.lower(), market, now(), rolle, art,
                  inhalt if isinstance(inhalt, str) else json.dumps(inhalt, ensure_ascii=False),
                  model, kosten))
    conn.commit()


# -- Kontext ------------------------------------------------------------------
def _cache(conn, source, keyword):
    r = conn.execute("SELECT payload FROM kw_cache WHERE source=? AND keyword=?",
                     (source, keyword.lower())).fetchone()
    if not r:
        return None
    try:
        return json.loads(r["payload"])
    except ValueError:
        return None


def kontext(conn, market, keyword):
    """Alles Gemessene zu einem Keyword. Fehlt etwas, bleibt es None."""
    kw = keyword.strip().lower()
    row = conn.execute("SELECT * FROM keyword_state WHERE keyword=? AND market=?",
                       (kw, market)).fetchone()
    row = dict(row) if row else {}
    m = _cache(conn, "dataforseo", kw)
    m = m if isinstance(m, dict) else {}
    serp = _cache(conn, "dataforseo:serp", kw) or {}

    treffer, arten = [], []
    for o in (serp.get("organics") or [])[:10]:
        v = _cache(conn, "page", (o.get("url") or "").lower()) or {}
        sicher = (v.get("kind", "unknown") != "unknown"
                  and (v.get("confidence") or 0) >= CONFIDENCE_FLOOR)
        arten.append(v.get("kind") if sicher else None)
        treffer.append({"rang": o.get("rank"), "domain": o.get("domain"),
                        "titel": (o.get("title") or "")[:90],
                        "art": v.get("kind", "unknown") if sicher else "unklar"})
    lvl = row.get("tier")
    if lvl is None:
        lvl = classify_intent(kw)[0]
    luecke, grund = ist_luecke(arten) if treffer else (False, "")
    if lvl not in (1, 2, 3):
        luecke = False

    varianten = []
    if row.get("subject"):
        for r in conn.execute(
                "SELECT keyword, volume, cpc, difficulty FROM keyword_state"
                " WHERE market=? AND subject=? AND keyword<>? ORDER BY volume DESC LIMIT 8",
                (market, row["subject"], kw)):
            varianten.append(dict(r))

    gespeichert = typen.gespeichert(conn, market)
    return {
        "keyword": kw,
        "typ": typen.typ(kw, gespeichert),
        "stufe": lvl, "stufe_name": tier_label(lvl),
        "volumen": m.get("search_volume", row.get("volume")),
        "cpc": m.get("cpc", row.get("cpc")),
        "gebot": [m.get("low_bid"), m.get("high_bid")],
        "wettbewerb": m.get("competition_index"),
        "monate": [[x.get("year"), x.get("month"), x.get("search_volume")]
                   for x in (m.get("monthly") or [])],
        "difficulty": row.get("difficulty"), "streuung": row.get("spread"),
        "serp_art": row.get("serp_intent") or "", "signale": row.get("flags") or "",
        "trend": row.get("trend"), "saison": row.get("season"),
        "sicher": row.get("sure"), "unsicher": row.get("unsure"),
        "thema": row.get("subject") or "",
        "zustand": row.get("state") or "", "notiz": row.get("note") or "",
        "treffer": treffer,
        "treffer_arten": dict(Counter(a for a in arten if a)),
        "luecke": luecke, "luecke_grund": grund if luecke else "",
        "verwandt": (serp.get("related") or [])[:8],
        "fragen": (serp.get("paa") or [])[:6],
        "varianten": varianten,
        "gemessen": bool(m) or bool(row),
    }


def kontext_text(k):
    """Die Messwerte als kompakter Text fuer das Modell."""
    def zahl(v, nk=0):
        if v is None:
            return "unbekannt"
        s = ("{:,.%df}" % nk).format(v)
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    zeilen = [
        "Keyword: %s" % k["keyword"],
        "Produkttyp nach Suchwörtern: %s" % dict((t[0], t[1]) for t in typen.TYPEN).get(
            k["typ"], "unklar"),
        "Absichtsstufe: %s %s" % (k["stufe"], k["stufe_name"]),
        "Volumen: %s Suchen pro Monat" % zahl(k["volumen"]),
        "CPC: %s €, Gebotsspanne %s bis %s €, Wettbewerb in Anzeigen %s von 100" % (
            zahl(k["cpc"], 2), zahl(k["gebot"][0], 2), zahl(k["gebot"][1], 2),
            zahl(k["wettbewerb"])),
    ]
    if k["monate"]:
        zeilen.append("Monatsverlauf (Jahr-Monat: Suchen): " + ", ".join(
            "%s-%02d: %s" % (y, mo, zahl(v)) for y, mo, v in k["monate"]))
    if k["trend"] is not None:
        zeilen.append("Trend: %+d %% gegenüber dem Quartal davor" % round((k["trend"] - 1) * 100))
    if k["saison"]:
        zeilen.append("Saison: %s" % zahl(k["saison"], 1))
    if k["difficulty"] is not None:
        zeilen.append("Difficulty: %s ± %s, Freiraum %s" % (
            k["difficulty"], zahl(k["streuung"], 0) if k["streuung"] else "?",
            100 - k["difficulty"]))
    else:
        zeilen.append("Difficulty: nicht belastbar, zu wenige sicher eingestufte Treffer")
    if k["treffer"]:
        zeilen.append("Seite 1 (%s sicher eingestuft, %s unklar), Charakter %s:" % (
            k["sicher"] if k["sicher"] is not None else "?",
            k["unsicher"] if k["unsicher"] is not None else "?", k["serp_art"] or "?"))
        for t in k["treffer"]:
            zeilen.append("  %s. %s [%s] %s" % (t["rang"], t["domain"],
                                               ART_NAMEN.get(t["art"], t["art"]), t["titel"]))
    else:
        zeilen.append("Seite 1: nicht gemessen")
    if k["signale"]:
        zeilen.append("Schwächesignale: %s" % k["signale"])
    zeilen.append("Lücke: %s" % ("ja, %s" % k["luecke_grund"] if k["luecke"] else "nein"))
    if k["varianten"]:
        zeilen.append("Varianten im selben Thema: " + "; ".join(
            "%s (%s Suchen, %s €, Difficulty %s)" % (
                v["keyword"], zahl(v["volume"]), zahl(v["cpc"], 2),
                v["difficulty"] if v["difficulty"] is not None else "?")
            for v in k["varianten"]))
    if k["fragen"]:
        zeilen.append("Fragen auf Google: " + " | ".join(k["fragen"]))
    if k["verwandt"]:
        zeilen.append("Verwandte Suchen: " + " | ".join(k["verwandt"]))
    if k["notiz"]:
        zeilen.append("Eigene Notiz: %s" % k["notiz"])
    return "\n".join(zeilen)


# -- Modellaufrufe --------------------------------------------------------------
def parse_einschaetzung(text):
    """Prueft die JSON-Antwort und bringt sie in feste Form."""
    if not text or not text.strip():
        raise LLMFehler("leere Antwort")
    try:
        d = json.loads(text)
    except ValueError:
        start, ende = text.find("{"), text.rfind("}")
        if start < 0 or ende <= start:
            raise LLMFehler("Antwort ist kein JSON")
        d = json.loads(text[start:ende + 1])
    urteil = str(d.get("urteil") or "").strip().rstrip(".")
    if not urteil:
        raise LLMFehler("Antwort ohne Urteil")
    try:
        ton = max(0.0, min(1.0, float(d.get("ton"))))
    except (TypeError, ValueError):
        ton = 0.5
    abschnitte = []
    for a in d.get("abschnitte") or []:
        if isinstance(a, dict) and a.get("text"):
            abschnitte.append({"titel": str(a.get("titel") or "").strip() or "Hinweis",
                               "text": str(a["text"]).strip()})
    return {"urteil": urteil, "ton": ton, "abschnitte": abschnitte}


def _als_text(e):
    return "%s\n%s" % (e["urteil"], "\n".join("%s: %s" % (a["titel"], a["text"])
                                                for a in e["abschnitte"]))


def kosten_einschaetzung(conn, market, keyword, model):
    k = kontext(conn, market, keyword)
    eingabe = tokens_ungefaehr(SYSTEM + FORMAT + kontext_text(k))
    return round(schaetzung(model, eingabe, 3500), 4)


def kosten_nachfrage(conn, market, keyword, model):
    k = kontext(conn, market, keyword)
    v = verlauf(conn, market, keyword)
    eingabe = tokens_ungefaehr(SYSTEM + DIALOG + kontext_text(k) + json.dumps(v, ensure_ascii=False))
    return round(schaetzung(model, eingabe, 1500), 4)


def einschaetzen(conn, market, keyword, run, llm, model):
    k = kontext(conn, market, keyword)
    if not k["gemessen"]:
        raise LLMFehler("Keyword ist noch nicht gemessen")
    messages = [{"role": "system", "content": SYSTEM + "\n\n" + FORMAT},
                {"role": "user", "content": kontext_text(k)}]
    letzter = None
    for _versuch in range(2):     # JSON-Modus kann leer antworten, dann einmal neu
        text, usage, used = llm.chat(messages, model=model, json_mode=True)
        _buchen(run, used, usage, "einschaetzung %s" % k["keyword"])
        try:
            e = parse_einschaetzung(text)
            break
        except (LLMFehler, ValueError) as err:
            letzter = err
    else:
        raise LLMFehler("Einschätzung unlesbar: %s" % letzter)
    _anhaengen(conn, market, k["keyword"], "user", "start", k["keyword"])
    _anhaengen(conn, market, k["keyword"], "assistant", "einschaetzung", e, used,
               round(run.total_cost(), 6))
    return verlauf(conn, market, k["keyword"])


def nachfragen(conn, market, keyword, frage, run, llm, model):
    k = kontext(conn, market, keyword)
    v = verlauf(conn, market, k["keyword"])
    messages = [{"role": "system", "content": SYSTEM + "\n\n" + DIALOG},
                {"role": "user", "content": "Messwerte:\n" + kontext_text(k)}]
    for m in v:
        if m["art"] == "start":
            continue
        inhalt = _als_text(m["inhalt"]) if m["art"] == "einschaetzung" else m["inhalt"]
        messages.append({"role": m["rolle"], "content": inhalt})
    messages.append({"role": "user", "content": frage})
    vorher = run.total_cost()
    text, usage, used = llm.chat(messages, model=model, aufwand="high", max_tokens=4000)
    _buchen(run, used, usage, "nachfrage %s" % k["keyword"])
    if not text:
        raise LLMFehler("leere Antwort")
    _anhaengen(conn, market, k["keyword"], "user", "frage", frage)
    _anhaengen(conn, market, k["keyword"], "assistant", "antwort", text, used,
               round(run.total_cost() - vorher, 6))
    return verlauf(conn, market, k["keyword"])


def _buchen(run, model, usage, note):
    from .llm import kosten
    run.charge("deepseek", model, "LLM", int(usage.get("total_tokens") or 0),
               kosten(model, usage), note=note)


def typen_einordnen(conn, market, keywords, run, llm, model):
    """Ordnet die Keywords, die keine Regel trifft, per Modell einem Typ zu."""
    if not keywords:
        return {}
    liste = "\n".join("- %s" % k for k in keywords)
    erlaubt = ", ".join("%s (%s)" % (t[0], t[2]) for t in typen.TYPEN)
    messages = [
        {"role": "system", "content":
            "Ordne jedes Keyword genau einem Produkttyp zu: was müsste man bauen, um "
            "diese Suche zu bedienen. Erlaubt: %s. Antworte als JSON in der Form "
            '{"zuordnung": {"keyword": "typ"}}.' % erlaubt},
        {"role": "user", "content": liste}]
    text, usage, used = llm.chat(messages, model=model, json_mode=True, denken=False,
                                 max_tokens=2000)
    _buchen(run, used, usage, "typen %d" % len(keywords))
    try:
        z = json.loads(text).get("zuordnung") or {}
    except (ValueError, AttributeError):
        raise LLMFehler("Typ-Zuordnung unlesbar")
    z = dict((k.lower(), v) for k, v in z.items() if v in typen.IDS and k.lower() in
             set(x.lower() for x in keywords))
    typen.speichern(conn, market, z, "llm:%s" % used, now())
    return z


__all__ = ["ABSCHNITTE", "ANTWORTET", "verlauf", "themen", "kontext", "kontext_text",
           "parse_einschaetzung", "einschaetzen", "nachfragen", "typen_einordnen",
           "kosten_einschaetzung", "kosten_nachfrage"]
