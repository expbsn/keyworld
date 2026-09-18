"""Verdichtung je Themengebiet.

'dienstplan vorlage', 'dienstplan excel vorlage' und 'dienstplan vorlage word'
sind dieselbe Gelegenheit. 72 Zeilen sind in Wahrheit etwa 25 Chancen.

Verdichtet wird nach Gebiet, nicht nach Wortaehnlichkeit -- das Gebiet ist
bereits bekannt, weil der Generator daraus erzeugt hat.
"""
from collections import OrderedDict


def best_of(rows):
    """Die vertretbarste Zeile einer Gruppe: niedrigste Difficulty, bei
    Gleichstand hoechster CPC. Unbekannte Difficulty gilt als sehr niedrig --
    Fehlerbias: im Zweifel nach oben."""
    def key(r):
        s = r.get("serp") or {}
        d = s.get("difficulty")
        return (0 if d is None else 1, d or 0, -(r.get("cpc") or 0.0))
    return sorted(rows, key=key)[0]


def group(rows, subject_of):
    """-> OrderedDict {gebiet: {'best': row, 'variants': [rows], 'n': int}}

    Die Gruppen selbst sind nach der Difficulty ihrer besten Zeile sortiert,
    damit die Verdichtung dieselbe Sortierachse behaelt wie die Liste.
    """
    buckets = {}
    for r in rows:
        buckets.setdefault(subject_of(r["keyword"]), []).append(r)

    out = []
    for subject, members in buckets.items():
        b = best_of(members)
        others = [m for m in members if m is not b]
        others.sort(key=lambda r: -(r.get("search_volume") or 0))
        out.append((subject, {"best": b, "variants": others, "n": len(members)}))

    def gkey(item):
        s = item[1]["best"].get("serp") or {}
        d = s.get("difficulty")
        return (0 if d is None else 1, d or 0, -(item[1]["best"].get("cpc") or 0.0))

    return OrderedDict(sorted(out, key=gkey))


def summary(groups):
    return {"gebiete": len(groups),
            "zeilen": sum(g["n"] for g in groups.values()),
            "verdichtung": round(sum(g["n"] for g in groups.values()) / max(1, len(groups)), 1)}
