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
MAX_KW_PER_KD = 1000            # bulk_keyword_difficulty
KD_REQUEST_COST = 0.012         # Fixkosten pro Request
KD_ITEM_COST = 0.00012          # je Keyword
MAX_SERP_TASKS = 100            # Tasks pro task_post
SERP_LIVE_COST = 0.002          # je Keyword, Antwort in Sekunden
SERP_QUEUED_COST = 0.0006       # je Keyword, Standard-Queue


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

    def _poll(self, path_base, task_id, max_wait=900):
        """Holt einen Queue-Task ab. task_get ist kostenlos."""
        waited, interval = 0.0, 3.0
        while waited < max_wait:
            data = self._get("%s/task_get/%s" % (path_base, task_id))
            task = (data.get("tasks") or [{}])[0]
            code = task.get("status_code")
            if code == 20000:
                return task.get("result") or [], True
            if code in (40601, 40602, 20100):     # handed / in queue / created
                time.sleep(interval)
                waited += interval
                interval = min(interval * 1.5, 30.0)
                continue
            raise DataForSEOError("task_get %s: %s %s" % (task_id, code, task.get("status_message")))
        return [], False

    def _dispatch(self, path_base, payload, run, layer, units, note):
        """Live oder Queue -- gibt (items, gemeldete_kosten) zurueck.

        Im Queue-Modus wird der Task erst registriert und dann abgeholt. Bricht
        das Abholen ab, bleibt er in pending_tasks und kostet beim spaeteren
        Einsammeln nichts mehr.
        """
        if self.live:
            data = self._post("%s/live" % path_base, payload)
            return self._results(data), float(data.get("cost") or 0.0)

        data = self._post("%s/task_post" % path_base, payload)
        cost = float(data.get("cost") or 0.0)
        ids = [t.get("id") for t in (data.get("tasks") or []) if t.get("id")]
        for tid in ids:
            run.add_pending(tid, self.name, path_base, layer, cost / max(1, len(ids)), units)
        items = []
        for tid in ids:
            got, done = self._poll(path_base, tid)
            items.extend(got)
            if done:
                run.mark_fetched(tid)
        return items, cost

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

        n_hits = len(keywords) - len(misses)
        if n_hits:
            # Ersparnis anteilig: was diese Keywords als eigene Tasks gekostet haetten
            saved, _ = self.estimate_search_volume_cost(n_hits)
            run.charge(self.name, "search_volume", layer, n_hits, saved,
                       cached=True, note="%d Cache-Treffer" % n_hits)

        if misses:
            est, tasks = self.estimate_search_volume_cost(len(misses))
            if run.would_exceed(est):
                raise DataForSEOError(
                    "Abbruch: %d Keywords wuerden ~%.4f USD kosten, Budget-Rest %.4f USD"
                    % (len(misses), est, run.budget - run.total_cost()))

        base = "keywords_data/google_ads/search_volume"
        for i in range(0, len(misses), MAX_KW_PER_TASK):
            chunk = misses[i:i + MAX_KW_PER_TASK]
            payload = [{"keywords": chunk,
                        "location_code": Config.location_code,
                        "language_code": Config.language_code,
                        "search_partners": False,
                        "sort_by": "search_volume"}]
            items, billed = self._dispatch(base, payload, run, layer, len(chunk), "search_volume")
            for item in items:
                m = self._parse(item)
                run.cache_put(self.name, m.keyword, m.to_dict())
                results.append(m)
            run.charge(self.name, "search_volume", layer, len(chunk), billed,
                       note="%d Keywords -> %d Treffer" % (len(chunk), len(items)))
            run.commit()

        return results

    def estimate_difficulty_cost(self, n_keywords):
        reqs = max(1, math.ceil(n_keywords / MAX_KW_PER_KD))
        return reqs * KD_REQUEST_COST + n_keywords * KD_ITEM_COST, reqs

    def keyword_difficulty(self, keywords, run, layer="L2"):
        """Difficulty 0-100 fuer bereits gefilterte Keywords. -> {keyword: kd}"""
        keywords = list(dict.fromkeys(k.strip().lower() for k in keywords if k and k.strip()))
        if not keywords:
            return {}

        out, misses = {}, []
        for kw in keywords:
            hit = run.cache_get("%s:kd" % self.name, kw)
            if hit is not None:
                # 0 heisst bei DataForSEO "keine SERP-Daten", nicht "trivial".
                # Als Unbekannt fuehren, sonst vergiftet es jede aufsteigende Sortierung.
                out[kw] = hit.get("keyword_difficulty") or None
            else:
                misses.append(kw)

        if len(out):
            saved, _ = self.estimate_difficulty_cost(len(out))
            run.charge(self.name, "keyword_difficulty", layer, len(out), saved,
                       cached=True, note="%d Cache-Treffer" % len(out))
        if not misses:
            return out

        est, _ = self.estimate_difficulty_cost(len(misses))
        if run.would_exceed(est):
            raise DataForSEOError(
                "Abbruch: Difficulty fuer %d Keywords kostet ~%.4f USD, Budget-Rest %.4f USD"
                % (len(misses), est, run.budget - run.total_cost()))

        for i in range(0, len(misses), MAX_KW_PER_KD):
            chunk = misses[i:i + MAX_KW_PER_KD]
            payload = [{"keywords": chunk,
                        "location_code": Config.location_code,
                        "language_code": Config.language_code}]
            data = self._post("dataforseo_labs/google/bulk_keyword_difficulty/live", payload)
            items = []
            for block in self._results(data):
                items.extend(block.get("items") or [])
            for it in items:
                kw = (it.get("keyword") or "").strip().lower()
                kd = it.get("keyword_difficulty")
                out[kw] = kd or None
                run.cache_put("%s:kd" % self.name, kw, {"keyword_difficulty": kd})
            run.charge(self.name, "keyword_difficulty", layer, len(chunk),
                       float(data.get("cost") or 0.0),
                       note="%d Keywords -> %d Werte" % (len(chunk), len(items)))
            run.commit()
        return out

    def estimate_serp_cost(self, n_keywords):
        return n_keywords * (SERP_LIVE_COST if self.live else SERP_QUEUED_COST)

    def _poll_many(self, path_base, task_ids, run, max_wait=600):
        """Holt viele Queue-Tasks ab. task_get ist kostenlos."""
        pending, out = dict(task_ids), {}
        waited, interval = 0.0, 8.0
        while pending and waited < max_wait:
            time.sleep(interval)
            waited += interval
            for tid in list(pending):
                data = self._get("%s/task_get/advanced/%s" % (path_base, tid))
                task = (data.get("tasks") or [{}])[0]
                code = task.get("status_code")
                if code == 20000:
                    for res in task.get("result") or []:
                        out[pending[tid]] = res
                    run.mark_fetched(tid)
                    pending.pop(tid)
                elif code not in (40601, 40602, 20100):
                    pending.pop(tid)      # dauerhafter Fehler, nicht weiter pollen
            interval = min(interval * 1.3, 30.0)
        return out

    def serp_batch(self, keywords, run, layer="L2", depth=10):
        """Top-10 SERP je Keyword. -> {keyword: geparste_serp}"""
        from ..serp import parse_serp
        keywords = list(dict.fromkeys(k.strip().lower() for k in keywords if k and k.strip()))
        if not keywords:
            return {}

        out, misses = {}, []
        for kw in keywords:
            hit = run.cache_get("%s:serp" % self.name, kw, ttl_days=14)
            if hit is not None:
                out[kw] = hit
            else:
                misses.append(kw)

        if out:
            run.charge(self.name, "serp", layer, len(out), self.estimate_serp_cost(len(out)),
                       cached=True, note="%d Cache-Treffer" % len(out))
        if not misses:
            return out

        est = self.estimate_serp_cost(len(misses))
        if run.would_exceed(est):
            raise DataForSEOError(
                "Abbruch: SERP fuer %d Keywords kostet ~%.4f USD, Budget-Rest %.4f USD"
                % (len(misses), est, run.budget - run.total_cost()))

        base = "serp/google/organic"
        def task(kw):
            return {"keyword": kw, "location_code": Config.location_code,
                    "language_code": Config.language_code, "device": "desktop",
                    "depth": depth}

        if self.live:
            for kw in misses:
                data = self._post("%s/live/advanced" % base, [task(kw)])
                for res in self._results(data):
                    out[kw] = parse_serp(res)
                    run.cache_put("%s:serp" % self.name, kw, out[kw])
                run.charge(self.name, "serp", layer, 1, float(data.get("cost") or 0.0),
                           note=kw)
                run.commit()
            return out

        for i in range(0, len(misses), MAX_SERP_TASKS):
            chunk = misses[i:i + MAX_SERP_TASKS]
            data = self._post("%s/task_post" % base, [task(k) for k in chunk])
            billed = float(data.get("cost") or 0.0)
            ids = {}
            for t, kw in zip(data.get("tasks") or [], chunk):
                if t.get("id"):
                    ids[t["id"]] = kw
                    run.add_pending(t["id"], self.name, base, layer,
                                    billed / max(1, len(chunk)), 1)
            run.charge(self.name, "serp", layer, len(chunk), billed,
                       note="%d SERPs angefordert" % len(chunk))
            got = self._poll_many(base, ids, run)
            for kw, res in got.items():
                out[kw] = parse_serp(res)
                run.cache_put("%s:serp" % self.name, kw, out[kw])
            run.commit()
        return out

    def collect(self, task, run):
        """Sammelt einen bereits bezahlten Queue-Task ein. Kostet nichts."""
        items, done = self._poll(task["path_base"], task["task_id"], max_wait=60)
        metrics = [self._parse(i) for i in items]
        for m in metrics:
            run.cache_put(self.name, m.keyword, m.to_dict())
        if done:
            run.mark_fetched(task["task_id"])
        run.charge(self.name, "collect", task["layer"], len(metrics), 0.0,
                   note="Task %s eingesammelt (bereits bezahlt)" % task["task_id"][:18])
        run.commit()
        return metrics, done

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
        items, billed = self._dispatch("keywords_data/google_ads/keywords_for_keywords",
                                       payload, run, layer, len(seeds), "keyword_ideas")
        metrics = [self._parse(i) for i in items]
        for m in metrics:
            run.cache_put(self.name, m.keyword, m.to_dict())
        run.cache_put(self.name, cache_key, [m.to_dict() for m in metrics])
        run.charge(self.name, "keywords_for_keywords", layer, len(metrics), billed,
                   note="%d Seeds -> %d Ideen" % (len(seeds), len(metrics)))
        run.commit()
        return metrics[:limit]
