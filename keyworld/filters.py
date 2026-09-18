"""Harte Filterkette. Kein gemischter Score.

Die Reihenfolge ist Absicht: Volumen ist Mindestvoraussetzung, CPC ist der
Beweis, dass jemand an diesen Suchenden Geld verdient. Erst danach wird
ueberhaupt Difficulty eingekauft -- so zahlt man nur fuer Ueberlebende.
"""
from collections import OrderedDict

from .intent import classify

DEFAULTS = {
    "min_volume": 300,      # darunter: zu duenn fuer ein Produkt
    "max_volume": 10000,    # darueber: sitzt schon jemand mit Budget
    "min_cpc": 0.50,        # darunter: Informationssuche, kein Geld im Markt
    "min_trend": None,      # 0.8 = schrumpfende Nischen raus. Standard aus:
}                           # eine Falle sichtbar machen schlaegt sie wegzufiltern


class Funnel:
    """Fuehrt die Filter nacheinander aus und protokolliert jeden Abgang."""

    def __init__(self, min_volume=None, max_volume=None, min_cpc=None, min_trend=None):
        self.min_volume = DEFAULTS["min_volume"] if min_volume is None else min_volume
        self.max_volume = DEFAULTS["max_volume"] if max_volume is None else max_volume
        self.min_cpc = DEFAULTS["min_cpc"] if min_cpc is None else min_cpc
        self.min_trend = min_trend
        self.stages = OrderedDict()
        self.dropped = {}

    def _step(self, label, items, keep):
        before = len(items)
        survivors = [m for m in items if keep(m)]
        self.dropped[label] = [m for m in items if not keep(m)]
        self.stages[label] = {"raus": before - len(survivors), "uebrig": len(survivors)}
        return survivors

    def run_serp_stage(self, metrics, analysis, require_product_serp=False):
        """Vierte Stufe, erst nach Layer 2 moeglich: SERPs ohne jede Produktseite.

        Standardmaessig nur markiert, nicht gefiltert -- die Entscheidung
        gehoert dem Nutzer, nicht dem Tool.
        """
        if not require_product_serp:
            return list(metrics)
        keep = lambda m: (analysis.get(m.keyword) or {}).get("serp_intent") != "RED"
        return self._step("SERP ohne Produktseiten", list(metrics), keep)

    def run(self, metrics):
        items = list(metrics)
        self.stages["Kandidaten"] = {"raus": 0, "uebrig": len(items)}

        items = self._step("Volumen < %d" % self.min_volume, items,
                           lambda m: (m.search_volume or 0) >= self.min_volume)
        items = self._step("Volumen > %d" % self.max_volume, items,
                           lambda m: (m.search_volume or 0) <= self.max_volume)
        items = self._step("CPC < %.2f" % self.min_cpc, items,
                           lambda m: (m.cpc or 0.0) >= self.min_cpc)
        if self.min_trend:
            # Unbekannter Trend ueberlebt -- nicht aussortieren, was man nicht weiss
            items = self._step("Trend < %.2f" % self.min_trend, items,
                               lambda m: (m.trend_ratio() or 99) >= self.min_trend)
        return items

    def report_rows(self):
        return [{"stufe": k, "raus": v["raus"] or "", "uebrig": v["uebrig"]}
                for k, v in self.stages.items()]


def annotate_intent(metrics):
    """Haengt die Absichtsstufe an. Kostenlos, laeuft vor jedem Einkauf."""
    out = []
    for m in metrics:
        lvl, name, pat = classify(m.keyword)
        out.append((m, lvl, name, pat))
    return out


def order(annotated, difficulty_of):
    """Sortierung: eine Achse, Difficulty aufsteigend.

    Unbekannte Difficulty kommt ZUERST, nicht zuletzt. Fehlerbias: ein falsch
    aussortiertes Keyword kostet die ganze Nische, ein zu viel geprueftes
    zehn Minuten.
    """
    def key(row):
        m = row[0]
        kd = difficulty_of(m.keyword)
        return (0 if kd is None else 1, kd or 0, -(m.cpc or 0.0))
    return sorted(annotated, key=key)


def split_tiers(annotated):
    """Stufe 1-3 und Offen bleiben sichtbar, Stufe 4 sinkt."""
    from .intent import is_visible
    return ([a for a in annotated if is_visible(a[1])],
            [a for a in annotated if not is_visible(a[1])])
