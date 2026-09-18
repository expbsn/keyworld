"""CSV-Export. Primaeres Ausgabeformat -- bei 50-200 Zeilen schlaegt eine
sortierbare Tabelle jedes Diagramm.
"""
import csv
from pathlib import Path

COLUMNS = [
    ("keyword", "keyword"),
    ("tier", "tier"),
    ("tier_name", "tier_name"),
    ("difficulty", "difficulty"),
    ("confidence_sure", "sure"),
    ("review", "review"),
    ("flags", "flags"),
    ("volume", "search_volume"),
    ("cpc", "cpc"),
    ("competition", "competition_index"),
    ("trend", "trend_ratio"),
    ("serp_intent", "serp_intent"),
    ("product_share", "product_share"),
    ("unsure_results", "unsure"),
    ("top_domains", "top_domains"),
    ("unknown_domains", "unknown_domains"),
    ("state", "state"),
    ("note", "note"),
    ("market", "market"),
]


def _flat(row):
    s = row.get("serp") or {}
    out = {
        "keyword": row["keyword"],
        "tier": row["tier"],
        "tier_name": row["tier_name"],
        "difficulty": "" if s.get("difficulty") is None else s["difficulty"],
        "sure": s.get("sure", ""),
        "review": "ja" if s.get("review") else "",
        "flags": " ".join(s.get("flags", [])),
        "search_volume": row.get("search_volume") or "",
        "cpc": "" if row.get("cpc") is None else ("%.2f" % row["cpc"]),
        "competition_index": row.get("competition_index") or "",
        "trend_ratio": "" if row.get("trend_ratio") is None else row["trend_ratio"],
        "serp_intent": s.get("serp_intent", ""),
        "product_share": "" if s.get("product_share") is None else s["product_share"],
        "unsure": s.get("unsure", ""),
        "top_domains": " ".join(s.get("top_domains", [])[:5]),
        "unknown_domains": " ".join(s.get("unknown_domains", [])[:5]),
        "state": row.get("state", "new"),
        "note": row.get("note", ""),
        "market": row.get("market", ""),
    }
    return out


def write_csv(path, rows):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[c for c, _ in COLUMNS],
                           extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = _flat(r)
            w.writerow(dict((c, flat.get(k, "")) for c, k in COLUMNS))
    return str(p)


def write_review_csv(path, queue):
    """Pruefliste im Format von overrides.csv -- direkt einfuegbar."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["domain", "type", "note"])
        for r in queue:
            w.writerow([r["domain"], "", "%dx gesehen  %s" % (r["hits"], r["url"][:90])])
    return str(p)
