"""SERP-Analyse: Difficulty aus der Zusammensetzung der Top 10 ableiten.

Warum nicht den fertigen Keyword-Difficulty-Wert kaufen: der ist bei
Long-Tail-Keywords zu 59 % leer, und genau dort liegen die Nischen.

Warum nicht einfach Domain-Vielfalt zaehlen: zehn verschiedene Domains koennen
zehn etablierte Hersteller sein. Vielfalt allein misst nichts.

Was wirklich zaehlt, ist wer die Plaetze haelt. Ein Ratgeber-Artikel, ein
Forenthread oder eine Amazon-Suchergebnisseite sind von einer echten
Produktseite angreifbar. Eine echte Produktseite ist es nicht.
"""
import re
from collections import Counter
from urllib.parse import urlparse

# -- Ergebnistypen ------------------------------------------------------------
UGC_DOMAINS = {
    "reddit.com", "quora.com", "gutefrage.net", "wer-weiss-was.de",
    "stackoverflow.com", "stackexchange.com", "superuser.com", "serverfault.com",
    "facebook.com", "instagram.com", "tiktok.com", "pinterest.de", "pinterest.com",
    "youtube.com", "x.com", "twitter.com", "linkedin.com", "xing.com",
    "wikipedia.org", "wikihow.com", "medium.com", "tumblr.com",
    "trustpilot.com", "proven-expert.com", "provenexpert.com",
}
UGC_PATTERNS = [r"forum", r"community", r"\bboard\b", r"-hilfe", r"frag[a-z]*\.",
                r"\.forumieren\.", r"gutefrage"]

AGGREGATOR_DOMAINS = {          # Vergleichsportale: niemand besitzt das Keyword
    "capterra.com", "capterra.com.de", "g2.com", "getapp.de", "getapp.com",
    "softwareadvice.com", "omr.com", "trusted.de", "sortlist.de", "wlw.de",
    "testberichte.de", "vergleich.org", "netzsieger.de", "expertentesten.de",
    "stiftung-warentest.de", "oekotest.de", "check24.de", "verivox.de",
}
MARKETPLACE_DOMAINS = {
    "amazon.de", "amazon.com", "ebay.de", "ebay.com", "etsy.com", "otto.de",
    "kaufland.de", "idealo.de", "galaxus.de", "alibaba.com", "aliexpress.com",
    "temu.com", "shop-apotheke.de", "docmorris.de",
}
PUBLISHER_DOMAINS = {           # Redaktionelle Reichweite, meist Affiliate
    "welt.de", "spiegel.de", "stern.de", "focus.de", "chip.de", "computerbild.de",
    "heise.de", "t3n.de", "giga.de", "netzwelt.de", "techstage.de", "t-online.de",
    "n-tv.de", "merkur.de", "rtl.de", "brigitte.de", "geo.de", "utopia.de",
}
EDITORIAL_PATHS = [r"/magazin", r"/ratgeber", r"/blog", r"/news", r"/vergleich",
                   r"/test", r"/guide", r"/wiki", r"/lexikon", r"/tipps",
                   r"/artikel", r"/journal", r"/academy", r"/wissen", r"/r/"]
EDITORIAL_TLDS = (".academy", ".blog", ".wiki", ".info", ".news", ".guru", ".tips")
# Titelmuster verraten Ratgeber-Inhalte auch dann, wenn Domain und Pfad schweigen
EDITORIAL_TITLE = [r"\bratgeber\b", r"\btipps?\b", r"so geht", r"solltest du",
                   r"vorteile", r"nachteile", r"\berkl(ae|ä)rt\b", r"\bwas ist\b",
                   r"\bwie \w+", r"\bwarum\b", r"worauf", r"alles (ue|ü)ber",
                   r"\banleitung\b", r"\bcheckliste\b", r"\bwissen\b",
                   r"\bvergleich\b", r"\btest\b", r"\bguide\b", r"\d+ (tipps|gruende)"]

# Angreifbar = alles ausser einer echten Produkt- oder Anbieterseite
WEAK = {"ugc", "aggregator", "editorial", "marketplace"}


def _norm(domain):
    d = (domain or "").lower().lstrip(".")
    return d[4:] if d.startswith("www.") else d


def _is_marketplace_search(url):
    """Amazon-Suchergebnisseite statt Produktseite: Google fand nichts Besseres."""
    return bool(re.search(r"[?&](k|q|_nkw)=|/s\?|/sch/", url or ""))


def classify_result(domain, url, title=None):
    """-> ugc | aggregator | marketplace | editorial | product"""
    d, u = _norm(domain), (url or "").lower()
    base = ".".join(d.split(".")[-2:]) if d.count(".") >= 1 else d

    if d in UGC_DOMAINS or base in UGC_DOMAINS:
        return "ugc"
    if any(re.search(p, d) for p in UGC_PATTERNS):
        return "ugc"
    if d in AGGREGATOR_DOMAINS or base in AGGREGATOR_DOMAINS:
        return "aggregator"
    if d in MARKETPLACE_DOMAINS or base in MARKETPLACE_DOMAINS:
        return "marketplace"
    if d in PUBLISHER_DOMAINS or base in PUBLISHER_DOMAINS:
        return "editorial"
    path = urlparse(u).path if u else ""
    if any(re.search(p, path) for p in EDITORIAL_PATHS):
        return "editorial"
    if d.endswith(EDITORIAL_TLDS):
        return "editorial"
    t = (title or "").lower()
    if t and any(re.search(p, t) for p in EDITORIAL_TITLE):
        return "editorial"
    return "product"


def parse_serp(result):
    """Zieht Organics, verwandte Suchen und PAA aus einer SERP-Antwort."""
    organics, related, paa = [], [], []
    for item in result.get("items") or []:
        t = item.get("type")
        if t == "organic":
            dom = _norm(item.get("domain"))
            organics.append({
                "rank": item.get("rank_group"),
                "domain": dom,
                "url": item.get("url"),
                "title": item.get("title"),
                "kind": classify_result(dom, item.get("url"), item.get("title")),
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


def analyse(serp, incumbents=None):
    """Difficulty 0-100 aus der SERP-Zusammensetzung. Niedrig = angreifbar.

    incumbents: Domains, die im gesamten gescouteten Set haeufig vorkommen.
    Ein Platzhirsch, der ueberall sitzt, ist teurer als zehn Einzelanbieter.
    """
    org = serp["organics"]
    if not org:
        return None
    n = len(org)
    kinds = Counter(o["kind"] for o in org)
    product_share = kinds["product"] / n
    weak_share = sum(kinds[k] for k in WEAK) / n
    diversity = len(set(o["domain"] for o in org)) / n

    inc = incumbents or set()
    incumbent_share = sum(1 for o in org if o["domain"] in inc) / n

    difficulty = 100.0 * (0.50 * product_share
                          + 0.35 * incumbent_share
                          + 0.15 * (1.0 - diversity))

    # Zeigt Google hier ueberhaupt Produktseiten? Eine niedrige Difficulty auf
    # einer rein redaktionellen SERP ist keine Chance, sondern die falsche
    # Absicht -- ein Produkt rankt dort gar nicht erst.
    if product_share >= 0.40:
        serp_intent = "PROD"
    elif product_share >= 0.20:
        serp_intent = "MIX"
    else:
        serp_intent = "RED"

    return {
        "difficulty": int(round(difficulty)),
        "serp_intent": serp_intent,
        "product_share": round(product_share, 2),
        "weak_share": round(weak_share, 2),
        "diversity": round(diversity, 2),
        "incumbent_share": round(incumbent_share, 2),
        "mix": dict(kinds),
        "ads": serp.get("ads", 0),
        "top_domains": [o["domain"] for o in org[:5]],
    }


def find_incumbents(serps, min_share=0.25):
    """Domains, die in mindestens min_share aller SERPs auftauchen."""
    if not serps:
        return set()
    seen = Counter()
    for s in serps.values():
        for d in set(o["domain"] for o in s["organics"]):
            seen[d] += 1
    cutoff = max(2, int(len(serps) * min_share))
    return {d for d, c in seen.items() if c >= cutoff}
