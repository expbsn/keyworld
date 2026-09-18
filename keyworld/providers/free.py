"""Layer 0 -- kostenlose Discovery ueber Autocomplete-Endpunkte.

Autosuggest spiegelt echte Nutzereingaben und enthaelt Long-Tail-Formulierungen,
die in Keyword-Datenbanken oft noch fehlen. Es liefert aber KEIN Volumen und
KEINEN CPC -- das macht Layer 1.
"""
import json
import random
import time

import requests

from ..config import Config
from .base import DiscoveryProvider

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# Modifier, die kommerzielle und Problem-Intention aufdecken
PREFIX_DE = ["wie", "was", "warum", "welche", "wo", "wann", "ist", "kann",
             "beste", "billige", "guenstige", "alternative zu"]
SUFFIX_DE = ["", " software", " app", " tool", " kaufen", " kosten", " preis",
             " alternative", " vergleich", " test", " erfahrungen", " fuer",
             " ohne", " vs", " selber machen", " online", " anbieter"]
ALPHABET = list("abcdefghijklmnopqrstuvwxyz")

AMAZON_MID = {"de": "A1PA6795UKMFR9", "en": "ATVPDKIKX0DER"}


class FreeDiscovery(DiscoveryProvider):
    name = "free-autocomplete"

    def __init__(self, lang=None, country=None, delay=(0.15, 0.4)):
        self.lang = lang or Config.language_code
        self.country = (country or self.lang).lower()
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    # -- Low level -------------------------------------------------------
    def _get(self, url, params, tries=3):
        for attempt in range(tries):
            try:
                r = self.session.get(url, params=params, timeout=10)
                if r.status_code == 200:
                    return r.text
                if r.status_code in (429, 503):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return None
            except requests.RequestException:
                time.sleep(0.8 * (attempt + 1))
        return None

    def _sleep(self):
        time.sleep(random.uniform(*self.delay))

    def google(self, query):
        txt = self._get("https://suggestqueries.google.com/complete/search",
                        {"client": "firefox", "hl": self.lang, "gl": self.country, "q": query})
        if not txt:
            return []
        try:
            return [s for s in json.loads(txt)[1] if isinstance(s, str)]
        except (ValueError, IndexError):
            return []

    def youtube(self, query):
        txt = self._get("https://suggestqueries.google.com/complete/search",
                        {"client": "youtube", "ds": "yt", "hl": self.lang, "q": query})
        if not txt or "(" not in txt:
            return []
        try:
            body = txt[txt.index("(") + 1: txt.rindex(")")]
            return [row[0] for row in json.loads(body)[1] if isinstance(row[0], str)]
        except (ValueError, IndexError):
            return []

    def amazon(self, query):
        mid = AMAZON_MID.get(self.lang, AMAZON_MID["en"])
        host = "completion.amazon.de" if self.lang == "de" else "completion.amazon.com"
        txt = self._get("https://%s/api/2017/suggestions" % host,
                        {"mid": mid, "alias": "aps", "prefix": query,
                         "client-info": "amazon-search-ui", "limit": 11})
        if not txt:
            return []
        try:
            return [s.get("value") for s in json.loads(txt).get("suggestions", []) if s.get("value")]
        except ValueError:
            return []

    # -- Public ----------------------------------------------------------
    def expand(self, seed, run, limit=200, deep=False, engines=("google", "amazon")):
        """Faechert einen Seed ueber Modifier auf. Kostet nichts, nur Zeit."""
        cached = run.cache_get(self.name, "%s|%s" % (seed, int(deep)), ttl_days=7)
        if cached is not None:
            run.charge(self.name, "expand", "L0", len(cached), 0.0, cached=True,
                       note="seed=%s" % seed)
            return cached[:limit]

        queries = ["%s%s" % (seed, s) for s in SUFFIX_DE]
        queries += ["%s %s" % (p, seed) for p in PREFIX_DE]
        if deep:
            queries += ["%s %s" % (seed, c) for c in ALPHABET]

        fns = {"google": self.google, "amazon": self.amazon, "youtube": self.youtube}
        found, calls = {}, 0
        for q in queries:
            for eng in engines:
                for s in fns[eng](q):
                    key = s.strip().lower()
                    if key and seed.split()[0].lower() in key:
                        found.setdefault(key, eng)
                calls += 1
                self._sleep()
            if len(found) >= limit * 3:
                break

        results = sorted(found.keys())
        run.cache_put(self.name, "%s|%s" % (seed, int(deep)), results)
        run.charge(self.name, "expand", "L0", len(results), 0.0,
                   note="seed=%s calls=%d" % (seed, calls))
        run.commit()
        return results[:limit]
