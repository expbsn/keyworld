"""Tiefenanalyse eines einzelnen Themengebiets.

Die Shortlist beantwortet 'welches Gebiet lohnt einen Blick'. Der Dive
beantwortet 'wie sieht dieses Gebiet wirklich aus' -- Saisonkurve, das
ganze Keyword-Umfeld, wer die SERPs haelt, und ob ueberhaupt jemand Geld
in die Hand nimmt.

Drei Umfaenge, weil Daten Geld kosten:

    schnell   nur was schon bezahlt ist                      0 $
    standard  + echtes Keyword-Umfeld von DataForSEO    ~0,12 $
    tief      + fehlende SERPs des Umfelds              ~0,20 $
"""
import json
import re
from collections import Counter

from .classify import CONFIDENCE_FLOOR
from .config import Config
from .intent import classify as classify_intent
from .providers.base import KeywordMetrics

SCOPES = ("schnell", "standard", "tief")
TIEF_MAX_SERPS = 25          # Deckel, damit ein Dive nie entgleist

# Referenzwerte aus den bisherigen Laeufen, um einen CPC einordnen zu koennen
CPC_REFERENZ = [
    ("Informationssuche, kein Markt", 0.50),
    ("schwach monetarisiert", 1.50),
    ("Verbrauchermarkt", 3.00),
    ("kleiner B2B-Markt", 8.00),
    ("harter B2B-Markt", 99.0),
]


UMLAUTE = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))
FUELLWORTER = r"\b(fuer|von|der|die|das|den|dem|ein|eine|einen|im|in|mit)\b"


def normform(keyword):
    """Vergleichsform: Umlaute gefaltet, Fuellwoerter weg, Woerter sortiert.

    Google bewertet 'fusspflege', 'fußpflege', 'fusspflege app' und 'app fuer fusspflege'
    als dieselbe Suche und meldet identische Zahlen. Sie zu addieren
    verdoppelt die Marktgroesse.
    """
    k = keyword.lower()
    for a, b in UMLAUTE:
        k = k.replace(a, b)
    k = re.sub(FUELLWORTER, " ", k)
    k = re.sub(r"[^a-z0-9 ]", " ", k)
    return " ".join(sorted(k.split()))


def dedupe(metrics):
    """Varianten zusammenfassen, die volumenstaerkste behalten."""
    best = {}
    for m in metrics:
        k = normform(m.keyword)
        if k not in best or (m.search_volume or 0) > (best[k].search_volume or 0):
            best[k] = m
    return list(best.values())


def stem(keyword):
    """Das erste Wort als Naeherung fuer das Gebiet."""
    parts = re.split(r"\s+", keyword.strip().lower())
    return parts[0] if parts else keyword.strip().lower()


def _metrics_from_cache(conn, needle):
    out = []
    for r in conn.execute("SELECT keyword, payload FROM kw_cache"
                          " WHERE source='dataforseo' AND keyword LIKE ?",
                          ("%" + needle + "%",)):
        try:
            d = json.loads(r["payload"])
        except ValueError:
            continue
        if isinstance(d, dict) and "search_volume" in d:
            out.append(KeywordMetrics(**dict(
                (k, v) for k, v in d.items()
                if k in KeywordMetrics.__dataclass_fields__)))
    return out


def _serps_from_cache(conn, keywords):
    want = set(keywords)
    out = {}
    for r in conn.execute("SELECT keyword, payload FROM kw_cache"
                          " WHERE source='dataforseo:serp'"):
        if r["keyword"] in want:
            try:
                out[r["keyword"]] = json.loads(r["payload"])
            except ValueError:
                pass
    return out


def _pages_from_cache(conn):
    """Schluessel sind kleingeschrieben -- der Cache normalisiert so. Wer mit
    der Original-URL nachschlaegt, verfehlt jede prozentkodierte Adresse und
    haelt sie faelschlich fuer unbekannt."""
    out = {}
    for r in conn.execute("SELECT keyword, payload FROM kw_cache WHERE source='page'"):
        try:
            out[r["keyword"].lower()] = json.loads(r["payload"])
        except ValueError:
            pass
    return out


def season_curve(metrics):
    """Monatsreihe des volumenstaerksten Keywords mit Historie."""
    best = None
    for m in metrics:
        if len(m.monthly) >= 12 and (best is None or
                                     (m.search_volume or 0) > (best.search_volume or 0)):
            best = m
    if not best:
        return None
    vals = [(x.get("year"), x.get("month"), x.get("search_volume") or 0)
            for x in best.monthly]
    peak = max(v for _y, _mo, v in vals) or 1
    tief = min(v for _y, _mo, v in vals)
    return {"keyword": best.keyword, "months": vals, "peak": peak, "trough": tief,
            "ratio": round(peak / max(1, tief), 1),
            "peak_month": max(vals, key=lambda t: t[2])[1],
            "avg": best.search_volume}


def monetarisierung(metrics):
    """Wie viel Geld steckt im Markt -- gewichtet nach Volumen.

    Vorher entdoppelt: Umlaut- und Wortstellungsvarianten meldet Google mit
    identischen Zahlen, addiert ergaeben sie eine zu grosse Marktgroesse.
    """
    metrics = dedupe(metrics)
    paare = [(m.cpc or 0.0, m.search_volume or 0) for m in metrics if m.search_volume]
    if not paare:
        return None
    vol = sum(v for _c, v in paare)
    gewichtet = sum(c * v for c, v in paare) / max(1, vol)
    hoechster = max(paare, key=lambda t: t[0])
    comps = [m.competition_index for m in metrics if m.competition_index is not None]
    urteil = next(name for name, grenze in CPC_REFERENZ if gewichtet < grenze)
    return {"cpc_gewichtet": round(gewichtet, 2),
            "cpc_max": round(hoechster[0], 2),
            "volumen_gesamt": vol,
            "keywords_entdoppelt": len(metrics),
            "competition_median": sorted(comps)[len(comps) // 2] if comps else None,
            "urteil": urteil}


def serp_landschaft(serps, pages):
    """Wer haelt die Plaetze im gesamten Umfeld, und welcher Art sind sie."""
    kinds, domains, dom_kind = Counter(), Counter(), {}
    for s in serps.values():
        for o in s.get("organics") or []:
            v = pages.get((o.get("url") or "").lower()) or {}
            kind = v.get("kind", "unknown")
            if v.get("confidence", 0) < CONFIDENCE_FLOOR:
                kind = "unknown"
            kinds[kind] += 1
            domains[o["domain"]] += 1
            dom_kind.setdefault(o["domain"], kind)
    gesamt = sum(kinds.values()) or 1
    return {"kinds": dict(kinds),
            "anteile": dict((k, round(v / gesamt, 2)) for k, v in kinds.items()),
            "top_domains": [{"domain": d, "hits": n, "kind": dom_kind.get(d, "unknown")}
                            for d, n in domains.most_common(15)],
            "serps": len(serps), "treffer": gesamt}


def fragen(serps, limit=25):
    paa, related = [], []
    for s in serps.values():
        paa += s.get("paa") or []
        related += s.get("related") or []
    return {"paa": list(dict.fromkeys(paa))[:limit],
            "related": list(dict.fromkeys(related))[:limit]}


def load_brands(path=None):
    """Markennamen aus data/brands.txt. Eine Marke ist kein Gebiet: ihr hoher
    CPC kommt daher, dass Wettbewerber auf einen fremden Namen bieten. Dort
    ist nichts zu betreten, dort hat jemand bereits gewonnen."""
    from pathlib import Path

    from .config import ROOT
    p = Path(path or ROOT / "data" / "brands.txt")
    if not p.exists():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip().lower()
        if line:
            out.add(line)
    return out


def compare(subjects, conn, pages=None):
    """Der schnelle Dive ueber viele Gebiete auf einmal, rein aus dem Cache.

    Beantwortet die Frage vor der Frage: welches Gebiet verdient ueberhaupt
    einen bezahlten Dive. Die Caches werden einmal geladen statt je Gebiet --
    sonst skaliert das quadratisch.
    """
    pages = _pages_from_cache(conn) if pages is None else pages
    alle_serps = {}
    for r in conn.execute("SELECT keyword, payload FROM kw_cache"
                          " WHERE source='dataforseo:serp'"):
        try:
            alle_serps[r["keyword"]] = json.loads(r["payload"])
        except ValueError:
            pass

    marken = load_brands()
    out = []
    for subject in dict.fromkeys(s.strip().lower() for s in subjects if s.strip()):
        metrics = _metrics_from_cache(conn, subject)
        if not metrics:
            continue
        serps = dict((k, v) for k, v in alle_serps.items() if subject in k)
        geld = monetarisierung(metrics)
        season = season_curve(metrics)
        land = serp_landschaft(serps, pages)
        anteile = land["anteile"]
        out.append({
            "subject": subject,
            "ist_marke": subject in marken,
            "keywords": len(metrics),
            "volumen": geld["volumen_gesamt"] if geld else 0,
            "cpc": geld["cpc_gewichtet"] if geld else None,
            "cpc_max": geld["cpc_max"] if geld else None,
            "competition": geld["competition_median"] if geld else None,
            "urteil": geld["urteil"] if geld else "keine Daten",
            "saison": season["ratio"] if season else None,
            "serps": land["serps"],
            "produkt_anteil": anteile.get("product", 0.0),
            "gratis_anteil": round(anteile.get("download", 0.0)
                                   + anteile.get("marketplace", 0.0), 2),
            "unklar_anteil": anteile.get("unknown", 0.0),
        })
    out.sort(key=lambda r: -(r["cpc"] or 0))
    return out


def estimate(scope, n_missing_serps, dfs, ideen_gecacht=False):
    """Kosten VOR dem Abruf. Gibt (usd, beschreibung) zurueck.

    Gecachte Teile werden abgezogen -- sonst kuendigt der Dive Kosten an,
    die gar nicht anfallen (gemessen: 0,1350 angekuendigt, 0,0036 bezahlt).
    """
    if scope == "schnell":
        return 0.0, "nur bereits bezahlte Daten"
    ideen = 0.0 if ideen_gecacht else dfs.estimate_search_volume_cost(1)[0]
    volumen = 0.0 if ideen_gecacht else dfs.estimate_search_volume_cost(1000)[0]
    if scope == "standard":
        return round(ideen + volumen, 4), "Keyword-Umfeld und Volumen"
    serps = dfs.estimate_serp_cost(min(n_missing_serps, TIEF_MAX_SERPS))
    return round(ideen + volumen + serps, 4), "Umfeld, Volumen und %d SERPs" % min(
        n_missing_serps, TIEF_MAX_SERPS)


def run_dive(keyword, run, dfs, scope="schnell", max_serps=TIEF_MAX_SERPS):
    """Fuehrt den Dive aus. Kauft nur, was der Umfang erlaubt."""
    if scope not in SCOPES:
        raise ValueError("Umfang muss einer von %s sein" % (SCOPES,))
    conn = run.conn
    subject = stem(keyword)
    gekauft = []

    if scope in ("standard", "tief"):
        seeds = [keyword, subject]
        ideen = dfs.keyword_ideas(list(dict.fromkeys(seeds)), run, limit=1000, layer="DIVE")
        gekauft.append("%d Keyword-Ideen" % len(ideen))
        neue = [m.keyword for m in ideen if subject in m.keyword]
        if neue:
            dfs.search_volume(neue[:1000], run, layer="DIVE")
            gekauft.append("Volumen fuer %d" % len(neue[:1000]))

    metrics = _metrics_from_cache(conn, subject)
    metrics.sort(key=lambda m: -(m.search_volume or 0))
    serps = _serps_from_cache(conn, [m.keyword for m in metrics])

    if scope == "tief":
        # Kein fester Schwellwert: in einem kleinen B2B-Feld liegen die
        # aufschlussreichsten Keywords unter jeder Schwelle, die in einem
        # grossen Feld sinnvoll waere. Stattdessen schlicht die
        # volumenstaerksten ohne SERP -- metrics ist bereits sortiert.
        fehlend = [m.keyword for m in metrics
                   if m.keyword not in serps and (m.search_volume or 0) > 0]
        fehlend = fehlend[:max_serps]
        if fehlend:
            neu = dfs.serp_batch(fehlend, run, layer="DIVE")
            from .serp import parse_serp  # noqa: F401  (serp_batch parst bereits)
            serps.update(neu)
            gekauft.append("%d SERPs" % len(neu))
            targets, seen = [], set()
            for s in neu.values():
                for o in s.get("organics") or []:
                    if o.get("url") and o["url"] not in seen:
                        seen.add(o["url"])
                        targets.append({"domain": o["domain"], "url": o["url"]})
            if targets:
                from .cli import _classify_cached
                from .classify import load_overrides
                _classify_cached(targets, load_overrides(), run, 10)
                gekauft.append("%d Seiten eingestuft" % len(targets))

    pages = _pages_from_cache(conn)
    reihen = []
    for m in metrics:
        lvl, name, _p = classify_intent(m.keyword)
        reihen.append({"keyword": m.keyword, "volume": m.search_volume, "cpc": m.cpc,
                       "competition": m.competition_index, "tier": lvl,
                       "tier_name": name, "trend": m.trend_ratio(),
                       "season": m.seasonality(), "hat_serp": m.keyword in serps})

    return {"keyword": keyword, "subject": subject, "scope": scope,
            "market": Config.market(), "gekauft": gekauft,
            "keywords": reihen,
            "season": season_curve(metrics),
            "geld": monetarisierung(metrics),
            "landschaft": serp_landschaft(serps, pages),
            "fragen": fragen(serps),
            "kosten": round(run.total_cost(), 4)}
