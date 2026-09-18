"""Layer 1/2 -- DataForSEO.

Preislogik (Stand 09/2026): Google Ads Search Volume kostet 0.06 USD pro Task
im Standard-Queue und 0.09 USD live, jeweils fuer bis zu 1000 Keywords. Wir
buendeln deshalb immer maximal -- ein Call fuer 1000 Keywords kostet genauso
viel wie einer fuer eins.

Die API meldet in jeder Response ein Feld `cost`. Wir buchen diesen gemeldeten
Ist-Wert in den Ledger, nie eine Schaetzung.
"""
import math
import time

import requests

from ..config import Config
from .base import KeywordMetrics, MetricsProvider

BASE = "https://api.dataforseo.com/v3"
MAX_KW_PER_TASK = 1000          # Search Volume
MAX_SEEDS_FOR_IDEAS = 20        # keywords_for_keywords


class DataForSEOError(RuntimeError):
    pass


class DataForSEO(MetricsProvider):
    name = "dataforseo"

    def __init__(self, login=None, password=None, live=True):
        self.login = login or Config.dataforseo_login
        self.password = password or Config.dataforseo_password
        self.live = live
        if not (self.login and self.password):
            raise DataForSEOError(
                "DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD fehlen -- siehe .env.example")
        self.session = requests.Session()
        self.session.auth = (self.login, self.password)
        self.session.headers.update({"Content-Type": "application/json"})

    # -- Low level -------------------------------------------------------
    def _post(self, path, payload, tries=3):
        url = "%s/%s" % (BASE, path.lstrip("/"))
        last = None
        for attempt in range(tries):
            try:
                r = self.session.post(url, json=payload, timeout=120)
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if data.get("status_code") == 20000:
                return data
            # 40202 = rate limit, 50000er = serverseitig -> retry lohnt
            if data.get("status_code") in (40202, 50000, 50100):
                time.sleep(2.0 * (attempt + 1))
                last = DataForSEOError(data.get("status_message"))
                continue
            raise DataForSEOError("%s: %s" % (data.get("status_code"), data.get("status_message")))
        raise DataForSEOError("Keine Antwort nach %d Versuchen: %s" % (tries, last))

    def _get(self, path):
        r = self.session.get("%s/%s" % (BASE, path.lstrip("/")), timeout=60)
        return r.json()

    @staticmethod
    def _results(data):
        out = []
        for task in data.get("tasks") or []:
            if task.get("status_code") != 20000:
                continue
            for item in task.get("result") or []:
                out.append(item)
        return out

    def _parse(self, item):
        return KeywordMetrics(
            keyword=(item.get("keyword") or "").strip().lower(),
            source=self.name,
            search_volume=item.get("search_volume"),
            cpc=item.get("cpc"),
            competition=item.get("competition"),
            competition_index=item.get("competition_index"),
            low_bid=item.get("low_top_of_page_bid"),
            high_bid=item.get("high_top_of_page_bid"),
            monthly=sorted(item.get("monthly_searches") or [],
                           key=lambda m: (m.get("year", 0), m.get("month", 0))),
            market=Config.market(),
        )

    # -- Public ----------------------------------------------------------
    def probe(self):
        """Verbindung + Guthaben pruefen. Dieser Call ist kostenlos."""
        try:
            data = self._get("appendix/user_data")
        except (requests.RequestException, ValueError) as e:
            return False, "Verbindung fehlgeschlagen: %s" % e
        if data.get("status_code") != 20000:
            return False, "Auth fehlgeschlagen: %s" % data.get("status_message")
        res = (data.get("tasks") or [{}])[0].get("result") or [{}]
        money = res[0].get("money", {})
        return True, "Guthaben: %.2f %s | Limit/Tag: %s" % (
            money.get("balance", 0.0), money.get("currency", "USD"),
            (money.get("limits") or {}).get("daily_limit", "n/a"))

    def estimate_search_volume_cost(self, n_keywords):
        tasks = max(1, math.ceil(n_keywords / MAX_KW_PER_TASK))
        return tasks * (0.09 if self.live else 0.06), tasks

    def search_volume(self, keywords, run, layer="L1"):
        """Exaktes Volumen, CPC, Competition und 12-Monats-Historie."""
        keywords = [k.strip().lower() for k in keywords if k and k.strip()]
        keywords = list(dict.fromkeys(keywords))
        if not keywords:
            return []

        results, misses = [], []
        for kw in keywords:
            hit = run.cache_get(self.name, kw)
            if hit is not None:
                m = KeywordMetrics(**{k: v for k, v in hit.items()
                                      if k in KeywordMetrics.__dataclass_fields__})
                m.cached = True
                results.append(m)
            else:
                misses.append(kw)

        if misses:
            est, tasks = self.estimate_search_volume_cost(len(misses))
            run.charge(self.name, "search_volume", layer, len(keywords) - len(misses), 0.0,
                       cached=True, note="%d Cache-Treffer" % (len(keywords) - len(misses)))
            if run.would_exceed(est):
                raise DataForSEOError(
                    "Abbruch: %d Keywords wuerden ~%.4f USD kosten, Budget-Rest %.4f USD"
                    % (len(misses), est, run.budget - run.total_cost()))

        mode = "live" if self.live else "task_post"
        for i in range(0, len(misses), MAX_KW_PER_TASK):
            chunk = misses[i:i + MAX_KW_PER_TASK]
            payload = [{"keywords": chunk,
                        "location_code": Config.location_code,
                        "language_code": Config.language_code,
                        "search_partners": False,
                        "sort_by": "search_volume"}]
            data = self._post("keywords_data/google_ads/search_volume/%s" % mode, payload)
            items = self._results(data)
            for item in items:
                m = self._parse(item)
                run.cache_put(self.name, m.keyword, m.to_dict())
                results.append(m)
            run.charge(self.name, "search_volume", layer, len(chunk),
                       float(data.get("cost") or 0.0),
                       note="%d Keywords -> %d Treffer" % (len(chunk), len(items)))
            run.commit()

        return results

    def keyword_ideas(self, seeds, run, limit=500, layer="L1"):
        """Erweitert Seeds zu verwandten Keywords -- inklusive Metriken."""
        seeds = [s.strip().lower() for s in seeds if s and s.strip()][:MAX_SEEDS_FOR_IDEAS]
        if not seeds:
            return []
        cache_key = "ideas|" + "|".join(sorted(seeds))
        hit = run.cache_get(self.name, cache_key)
        if hit is not None:
            run.charge(self.name, "keywords_for_keywords", layer, len(hit), 0.0, cached=True,
                       note="%d Seeds" % len(seeds))
            return [KeywordMetrics(**{k: v for k, v in d.items()
                                      if k in KeywordMetrics.__dataclass_fields__})
                    for d in hit][:limit]

        est = 0.09 if self.live else 0.06
        if run.would_exceed(est):
            raise DataForSEOError("Abbruch: Budget-Rest %.4f USD reicht nicht fuer ~%.4f USD"
                                  % (run.budget - run.total_cost(), est))

        payload = [{"keywords": seeds,
                    "location_code": Config.location_code,
                    "language_code": Config.language_code,
                    "search_partners": False,
                    "sort_by": "search_volume"}]
        mode = "live" if self.live else "task_post"
        data = self._post("keywords_data/google_ads/keywords_for_keywords/%s" % mode, payload)
        metrics = [self._parse(i) for i in self._results(data)]
        for m in metrics:
            run.cache_put(self.name, m.keyword, m.to_dict())
        run.cache_put(self.name, cache_key, [m.to_dict() for m in metrics])
        run.charge(self.name, "keywords_for_keywords", layer, len(metrics),
                   float(data.get("cost") or 0.0),
                   note="%d Seeds -> %d Ideen" % (len(seeds), len(metrics)))
        run.commit()
        return metrics[:limit]
