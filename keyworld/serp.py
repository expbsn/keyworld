"""SERP-Analyse: Schwaechesignale und Difficulty aus den Top 10.

Difficulty wird nur aus Treffern gerechnet, die sicher eingestuft sind.
Unsichere und unbekannte zaehlen NICHT als Produktseite -- sonst wuerde jede
unbekannte Domain die Difficulty hochtreiben und die Chance verstecken.

Bleiben zu wenige sichere Treffer uebrig, ist die Difficulty `None`. Das
Keyword wird dann nicht aussortiert, sondern zuerst gezeigt: ein falsch
Aussortiertes kostet die ganze Nische, ein zu viel geprueftes zehn Minuten.
"""
from collections import Counter

from .classify import CONFIDENCE_FLOOR, is_stale

MIN_CLASSIFIED = 3      # darunter ist jede Difficulty geraten
TOP_N = 5               # Schwaechesignale gelten fuer die oberen Plaetze


def parse_serp(result):
    """Organics, verwandte Suchen und PAA aus einer SERP-Antwort."""
    organics, related, paa = [], [], []
    for item in result.get("items") or []:
        t = item.get("type")
        if t == "organic":
            dom = (item.get("domain") or "").lower()
            organics.append({
                "rank": item.get("rank_group"),
                "domain": dom[4:] if dom.startswith("www.") else dom,
                "url": item.get("url"),
                "title": item.get("title"),
            })
        elif t == "related_searches":
            related.extend(item.get("items") or [])
        elif t == "people_also_ask":
            for q in item.get("items") or []:
                if q.get("title"):
                    paa.append(q["title"])
    return {"organics": organics,
            "related": [r for r in related if isinstance(r, str)],
            "paa": paa,
            "ads": sum(1 for i in (result.get("items") or [])
                       if i.get("type") in ("paid", "shopping"))}


def attach_verdicts(serp, verdicts):
    """Haengt die Seitenurteile an die Organics."""
    for o in serp["organics"]:
        v = verdicts.get(o["url"]) or {}
        o["kind"] = v.get("kind", "unknown")
        o["confidence"] = v.get("confidence", 0.0)
        o["lang"] = v.get("lang")
        o["published"] = v.get("published")
        o["download"] = v.get("download", False)
        o["why"] = v.get("why", [])
        o["sure"] = o["kind"] != "unknown" and o["confidence"] >= CONFIDENCE_FLOOR
    return serp


def weak_signals(serp, market_lang="de"):
    """Die Signale, die eine angreifbare SERP verraten. Nur die oberen Plaetze."""
    top = serp["organics"][:TOP_N]
    if not top:
        return [], {}
    sure = [o for o in top if o["sure"]]
    langs = [o["lang"] for o in top if o["lang"]]

    flags = []
    if any(o["kind"] == "forum" for o in sure):
        flags.append("FORUM")
    n_article = sum(1 for o in sure if o["kind"] == "article")
    if n_article >= 2:
        flags.append("ARTIKEL%d" % n_article)
    if any(is_stale(o["published"]) for o in top):
        flags.append("ALT")
    if len(langs) >= 2 and all(l != market_lang for l in langs):
        flags.append("NUR-EN")
    if any(o["download"] for o in top):
        flags.append("VORLAGE")
    if any(o["kind"] == "aggregator" for o in sure):
        flags.append("PORTAL")
    n_unsure = len(top) - len(sure)
    if n_unsure >= 3:
        flags.append("UNKLAR%d" % n_unsure)
    return flags, {"top_n": len(top), "sure_top": len(sure), "articles_top": n_article}


def analyse(serp, incumbents=None, market_lang="de"):
    """Difficulty 0-100 aus sicher eingestuften Treffern. Niedrig = angreifbar."""
    org = serp["organics"]
    if not org:
        return None
    sure = [o for o in org if o["sure"]]
    flags, detail = weak_signals(serp, market_lang)
    kinds = Counter(o["kind"] for o in org)
    inc = incumbents or set()
    incumbent_share = sum(1 for o in org if o["domain"] in inc) / len(org)
    diversity = len(set(o["domain"] for o in org)) / len(org)

    base = {
        "mix": dict(kinds),
        "unsure": len(org) - len(sure),
        "sure": len(sure),
        "flags": flags,
        "ads": serp.get("ads", 0),
        "diversity": round(diversity, 2),
        "incumbent_share": round(incumbent_share, 2),
        "top_domains": [o["domain"] for o in org[:5]],
        "unknown_domains": sorted(set(o["domain"] for o in org if not o["sure"])),
    }

    if len(sure) < MIN_CLASSIFIED:
        # Zu duenne Beleglage. Nicht raten, nicht aussortieren -- zeigen.
        base.update({"difficulty": None, "product_share": None,
                     "serp_intent": "?", "review": True,
                     "why": "nur %d von %d Treffern sicher eingestuft"
                            % (len(sure), len(org))})
        return base

    product_share = sum(1 for o in sure if o["kind"] == "product") / len(sure)
    difficulty = 100.0 * (0.50 * product_share
                          + 0.35 * incumbent_share
                          + 0.15 * (1.0 - diversity))
    if product_share >= 0.40:
        serp_intent = "PROD"
    elif product_share >= 0.20:
        serp_intent = "MIX"
    else:
        serp_intent = "RED"

    base.update({"difficulty": int(round(difficulty)),
                 "product_share": round(product_share, 2),
                 "serp_intent": serp_intent,
                 "review": base["unsure"] >= 3,
                 "why": ""})
    return base


def find_incumbents(serps, min_share=0.25):
    """Domains, die in mindestens min_share aller SERPs auftauchen."""
    if not serps:
        return set()
    seen = Counter()
    for s in serps.values():
        for d in set(o["domain"] for o in s["organics"]):
            seen[d] += 1
    cutoff = max(2, int(len(serps) * min_share))
    return set(d for d, c in seen.items() if c >= cutoff)


def review_queue(analysis, serps):
    """Domains, deren Einstufung unsicher ist -- Kandidaten fuer overrides.csv."""
    counts = Counter()
    example = {}
    for kw, s in serps.items():
        for o in s["organics"]:
            if not o.get("sure"):
                counts[o["domain"]] += 1
                example.setdefault(o["domain"], o["url"])
    return [{"domain": d, "hits": c, "url": example[d],
             "kind": "unknown", "note": ""} for d, c in counts.most_common()]
