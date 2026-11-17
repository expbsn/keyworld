"""Ein einzelnes Keyword messen, angestossen aus dem Interface.

Volumen und CPC, danach Seite 1 und die kostenlose Einstufung der Seiten.
Was im Cache liegt, kostet nichts. Live statt Warteschlange, weil jemand
vor dem Bildschirm wartet -- das kostet je Keyword ein paar Cent mehr.
"""
from .classify import load_overrides
from .dive import _metrics_from_cache, _pages_from_cache, _serps_from_cache, stem
from .intent import classify as classify_intent
from .providers.base import KeywordMetrics
from .serp import analyse, attach_verdicts, find_incumbents_grouped

SEITEN_PARALLEL = 8


def schaetzung(run, dfs, keyword):
    """Kosten vor dem Klick. -> (usd, was)"""
    kw = keyword.strip().lower()
    usd, was = 0.0, []
    if run.cache_get(dfs.name, kw) is None:
        usd += dfs.estimate_search_volume_cost(1)[0]
        was.append("Volumen und CPC")
    if run.cache_get("%s:serp" % dfs.name, kw, ttl_days=14) is None:
        usd += dfs.estimate_serp_cost(1)
        was.append("Seite 1 bei Google")
    return round(usd, 4), was


def messen(keyword, run, dfs, market):
    """Misst und speichert ein Keyword. -> dict mit dem Ergebnis."""
    from .cli import _classify_cached
    kw = keyword.strip().lower()
    metrics = dfs.search_volume([kw], run, layer="UI")
    m = next((x for x in metrics if x.keyword == kw), None)
    if m is None:
        # Keine Daten heisst kein Volumen. Merken, damit derselbe Versuch
        # nicht noch einmal bezahlt wird.
        m = KeywordMetrics(keyword=kw, search_volume=0)
        run.cache_put(dfs.name, kw, m.to_dict())
        run.commit()
    if not m.search_volume:
        return {"keyword": kw, "volumen": 0, "gemessen": True,
                "hinweis": "Kein messbares Suchvolumen. Seite 1 wurde nicht gekauft"}

    serps = dfs.serp_batch([kw], run, layer="UI")
    serp = serps.get(kw)
    if not serp:
        return {"keyword": kw, "volumen": m.search_volume, "gemessen": True,
                "hinweis": "Seite 1 konnte nicht geladen werden"}

    targets = [{"domain": o["domain"], "url": o["url"]}
               for o in serp["organics"] if o.get("url")]
    _classify_cached(targets, load_overrides(), run, SEITEN_PARALLEL)

    # Platzhirsche im Thema: dafuer alle schon gemessenen SERPs desselben Themas
    thema = stem(kw)
    pages = _pages_from_cache(run.conn)
    nachbarn = _serps_from_cache(run.conn, [x.keyword for x in _metrics_from_cache(run.conn, thema)])
    nachbarn[kw] = serp
    for s in nachbarn.values():
        attach_verdicts(s, dict((o["url"], pages.get((o.get("url") or "").lower()) or {})
                                for o in s["organics"]))
    inc = find_incumbents_grouped(nachbarn, dict((k, thema) for k in nachbarn))
    a = analyse(nachbarn[kw], inc.get(kw, set())) or {}

    lvl = classify_intent(kw)[0]
    run.upsert_keyword(
        kw, market, tier=lvl, difficulty=a.get("difficulty"), cpc=m.cpc,
        volume=m.search_volume, flags=" ".join(a.get("flags", [])),
        serp_intent=a.get("serp_intent", ""), trend=m.trend_ratio(), subject=thema,
        top_domains=" ".join(a.get("top_domains", [])[:4]), sure=a.get("sure"),
        unsure=a.get("unsure"), run_id=run.run_id, spread=a.get("spread"),
        season=m.seasonality())
    run.commit()
    return {"keyword": kw, "volumen": m.search_volume, "cpc": m.cpc,
            "difficulty": a.get("difficulty"), "gemessen": True}
