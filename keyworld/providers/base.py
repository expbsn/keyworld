"""Provider-Vertraege.

Jeder Provider meldet seine Kosten selbst an den Run-Ledger. Dadurch bleibt
die Ausgabenuebersicht korrekt, egal welches Backend eingesteckt ist.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class KeywordMetrics:
    """Normalisierte Keyword-Metriken -- providerunabhaengig."""
    keyword: str
    source: str = ""
    search_volume: Optional[int] = None
    cpc: Optional[float] = None
    competition: Optional[str] = None          # LOW | MEDIUM | HIGH
    competition_index: Optional[int] = None    # 0-100
    low_bid: Optional[float] = None
    high_bid: Optional[float] = None
    monthly: List[Dict] = field(default_factory=list)  # [{year, month, search_volume}]
    market: str = ""
    cached: bool = False

    def trend_ratio(self):
        """Letzte 3 Monate vs. die 3 davor. >1 = wachsend, None = zu wenig Daten."""
        vols = [m.get("search_volume") or 0 for m in self.monthly]
        if len(vols) < 6:
            return None
        recent, prior = sum(vols[-3:]), sum(vols[-6:-3])
        if prior == 0:
            return None
        return round(recent / prior, 3)

    def seasonality(self):
        """Spitze zu Median -- hoch = stark saisonal, also riskanter."""
        vols = [m.get("search_volume") or 0 for m in self.monthly if m.get("search_volume")]
        if len(vols) < 6:
            return None
        ordered = sorted(vols)
        median = ordered[len(ordered) // 2]
        return round(max(vols) / median, 2) if median else None

    def to_dict(self):
        d = asdict(self)
        d["trend_ratio"] = self.trend_ratio()
        d["seasonality"] = self.seasonality()
        return d


class DiscoveryProvider:
    """Layer 0: erzeugt Keyword-Kandidaten. Soll kostenlos oder sehr guenstig sein."""
    name = "discovery"

    def expand(self, seed, run, limit=200):
        raise NotImplementedError


class MetricsProvider:
    """Layer 1/2: reichert Kandidaten mit Volume, CPC und Wettbewerb an."""
    name = "metrics"

    def search_volume(self, keywords, run):
        """-> List[KeywordMetrics]"""
        raise NotImplementedError

    def keyword_ideas(self, seeds, run, limit=500):
        """-> List[KeywordMetrics] (Erweiterung MIT Metriken)"""
        raise NotImplementedError

    def probe(self):
        """-> (ok: bool, message: str) Verbindung und Guthaben pruefen."""
        raise NotImplementedError
