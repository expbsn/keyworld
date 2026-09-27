"""Lokales Interface. Stdlib-HTTP, keine Abhaengigkeit, kein Framework.

Duenne Schicht ueber denselben Funktionen, die die CLI nutzt -- es gibt
keine Logik, die nur hier existiert. Das Interface liest den
Zustandsspeicher und schreibt genau zwei Dinge zurueck: deinen Zustand je
Keyword und deine Korrekturen in overrides.csv.

Es scoutet nicht. Abrufen kostet Geld, und Geld gibt man auf der
Kommandozeile aus, nicht per Klick im Browser.
"""
import errno
import json
import socket
import urllib.error
import urllib.request
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .classify import CONFIDENCE_FLOOR, KINDS, add_override, load_overrides
from .config import Config
from .store import (Run, all_runs, connect, keyword_rows, lifetime_cost,
                    set_keyword_state, state_counts)

STATIC = Path(__file__).parent / "static"
STATES = ("new", "shortlisted", "reviewed", "rejected")


# -- Datenzugriff -------------------------------------------------------------
def shortlist(market, state=None, tier=None, serp=None, q=None, grouped=True):
    rows = keyword_rows(market, state)
    if tier not in (None, ""):
        rows = [r for r in rows if str(r["tier"]) == str(tier)]
    if serp:
        rows = [r for r in rows if (r["serp_intent"] or "") == serp]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r["keyword"]]
    def key0(r):
        d = r["difficulty"]
        return (1 if r["tier"] == 4 else 0, 0 if d is None else 1,
                d or 0, -(r["cpc"] or 0.0))

    if not grouped:
        return [dict(r, variants=[]) for r in sorted(rows, key=key0)]

    buckets = {}
    for r in rows:
        buckets.setdefault(r["subject"] or r["keyword"], []).append(r)

    def key(r):
        # Stufe 4 sinkt, wie in der CLI. Unbekannte Difficulty zuerst --
        # Fehlerbias: im Zweifel zeigen.
        d = r["difficulty"]
        return (1 if r["tier"] == 4 else 0, 0 if d is None else 1,
                d or 0, -(r["cpc"] or 0.0))

    out = []
    for _subject, members in buckets.items():
        members = sorted(members, key=key)
        best = dict(members[0])
        best["variants"] = [dict(m) for m in
                            sorted(members[1:], key=lambda r: -(r["volume"] or 0))]
        out.append(best)
    return sorted(out, key=key)


def pending_domains(market, limit=120):
    """Unsichere Domains aus den gecachten SERPs, nach Haeufigkeit."""
    conn = connect()
    verdicts = {}
    for r in conn.execute("SELECT keyword, payload FROM kw_cache WHERE source='page'"):
        try:
            verdicts[r["keyword"]] = json.loads(r["payload"])
        except ValueError:
            pass
    overrides = load_overrides()
    counts, example, why = Counter(), {}, {}
    for r in conn.execute("SELECT payload FROM kw_cache WHERE source='dataforseo:serp'"):
        try:
            serp = json.loads(r["payload"])
        except ValueError:
            continue
        for o in serp.get("organics") or []:
            url, dom = o.get("url"), o.get("domain")
            if not url or not dom:
                continue
            if any(s in overrides for s in _suffixes(dom)):
                continue
            v = verdicts.get(url.lower()) or verdicts.get(url)
            if v and v.get("kind") != "unknown" and v.get("confidence", 0) >= CONFIDENCE_FLOOR:
                continue
            counts[dom] += 1
            example.setdefault(dom, url)
            if v:
                why.setdefault(dom, "; ".join(v.get("why", []))[:90])
    return [{"domain": d, "hits": c, "url": example[d],
             "guess": why.get(d, "keine Signale")}
            for d, c in counts.most_common(limit)]


def _suffixes(domain):
    d = (domain or "").lower()
    d = d[4:] if d.startswith("www.") else d
    parts = d.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def _missing_serps(keyword):
    """Wie viele Keywords des Umfelds noch keine SERP haben."""
    from .dive import stem
    conn = connect()
    subject = stem(keyword)
    haben = set(r["keyword"] for r in conn.execute(
        "SELECT keyword FROM kw_cache WHERE source='dataforseo:serp'"))
    n = 0
    for r in conn.execute("SELECT keyword, payload FROM kw_cache"
                          " WHERE source='dataforseo' AND keyword LIKE ?",
                          ("%" + subject + "%",)):
        if r["keyword"] in haben:
            continue
        try:
            d = json.loads(r["payload"])
        except ValueError:
            continue
        if isinstance(d, dict) and (d.get("search_volume") or 0) >= 100:
            n += 1
    return n


class _Tarif:
    """Nur die Preisformeln, ohne Netz -- damit die Schaetzung auch ohne
    Credentials funktioniert und nie versehentlich etwas abruft."""
    name = "dataforseo"
    live = False

    def estimate_search_volume_cost(self, n):
        import math
        return max(1, math.ceil(n / 1000)) * 0.06, 1

    def estimate_serp_cost(self, n):
        return n * 0.0006


def dive_estimate(keyword, scope):
    """Kosten VOR dem Klick."""
    from .dive import TIEF_MAX_SERPS, estimate as _est
    fehlend = _missing_serps(keyword)
    usd, was = _est(scope, fehlend, _Tarif())
    return {"scope": scope, "usd": usd, "was": was,
            "fehlende_serps": min(fehlend, TIEF_MAX_SERPS)}


def run_dive_for_ui(keyword, scope):
    """Das Budget ist die gezeigte Schaetzung plus Reserve -- mehr als
    angekuendigt kann ueber das Interface nie ausgegeben werden."""
    from .dive import run_dive
    from .providers import DataForSEO
    if not Config.has_dataforseo():
        return {"error": "Keine DataForSEO-Credentials"}
    est = dive_estimate(keyword, scope)
    budget = round(est["usd"] * 1.5 + 0.01, 4)
    with Run(seed="dive:%s" % keyword, budget_usd=budget) as run:
        try:
            d = run_dive(keyword, run, DataForSEO(live=False), scope)
        except Exception as e:                            # noqa: BLE001
            return {"error": "%s: %s" % (type(e).__name__, e),
                    "ausgegeben": round(run.total_cost(), 4)}
        d["budget"] = budget
        d["ausgegeben"] = round(run.total_cost(), 4)
        d["geschaetzt"] = est["usd"]
        return d


def costs():
    spent, saved, calls = lifetime_cost()
    return {"spent": round(spent, 4), "saved": round(saved, 4), "calls": calls,
            "runs": all_runs(25), "states": state_counts(Config.market()),
            "market": Config.market()}


# -- HTTP ---------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(
            body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _guard(self, fn):
        """Ein Fehler im Handler soll eine lesbare Antwort geben, keinen
        abgebrochenen Socket -- sonst sieht das Interface nur Stille."""
        try:
            return fn()
        except Exception as e:                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        return self._guard(self._get)

    def do_POST(self):
        return self._guard(self._post)

    def _get(self):
        u = urlparse(self.path)
        qs = dict((k, v[0]) for k, v in parse_qs(u.query).items())
        market = Config.market()

        if u.path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(),
                              "text/html; charset=utf-8")
        if u.path == "/api/shortlist":
            return self._send(200, {
                "rows": shortlist(market, qs.get("state") or None,
                                  qs.get("tier") or None, qs.get("serp") or None,
                                  qs.get("q") or None,
                                  qs.get("grouped", "1") != "0"),
                "states": state_counts(market), "market": market})
        if u.path == "/api/queue":
            return self._send(200, {"rows": pending_domains(market),
                                    "kinds": [k for k in KINDS if k != "unknown"],
                                    "overrides": len(load_overrides())})
        if u.path == "/api/dive/estimate":
            kw = qs.get("keyword") or ""
            return self._send(200, dict(
                (sc, dive_estimate(kw, sc)) for sc in ("schnell", "standard", "tief")))
        if u.path == "/api/costs":
            return self._send(200, costs())
        return self._send(404, {"error": "not found"})

    def _post(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "kein JSON"})

        if u.path == "/api/state":
            kw = (body.get("keyword") or "").lower()
            state = body.get("state")
            if state and state not in STATES:
                return self._send(400, {"error": "unbekannter Zustand"})
            ok = set_keyword_state(kw, Config.market(), state, body.get("note"))
            return self._send(200 if ok else 404, {"ok": ok})

        if u.path == "/api/dive":
            kw = (body.get("keyword") or "").strip().lower()
            scope = body.get("scope") or "schnell"
            if not kw:
                return self._send(400, {"error": "keyword noetig"})
            if scope not in ("schnell", "standard", "tief"):
                return self._send(400, {"error": "unbekannter Umfang"})
            return self._send(200, run_dive_for_ui(kw, scope))

        if u.path == "/api/override":
            dom = (body.get("domain") or "").strip().lower()
            kind = (body.get("type") or "").strip().lower()
            if not dom or kind not in KINDS:
                return self._send(400, {"error": "domain und type noetig"})
            add_override(dom, kind, body.get("note") or "")
            return self._send(200, {"ok": True, "overrides": len(load_overrides())})

        return self._send(404, {"error": "not found"})


def is_keyworld(port, timeout=1.0):
    """Laeuft auf dem Port bereits eine Keyworld-Instanz?"""
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/costs" % port, timeout=timeout) as r:
            return "market" in json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, socket.timeout, OSError):
        return False


def serve(port=8765, open_browser=True, tries=8):
    """Bindet den Port. Ist er belegt, wird das erklaert statt geworfen."""
    srv, bound = None, port
    for offset in range(tries):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port + offset), Handler)
            bound = port + offset
            break
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            if offset == 0 and is_keyworld(port):
                url = "http://127.0.0.1:%d/" % port
                print("  Keyworld laeuft bereits auf %s -- oeffne nur den Browser." % url)
                print("  Beenden: pkill -f 'keyworld.cli serve'")
                if open_browser:
                    webbrowser.open(url)
                return 0
            print("  Port %d belegt, versuche %d ..." % (port + offset, port + offset + 1))
    if srv is None:
        print("  Kein freier Port zwischen %d und %d." % (port, port + tries - 1))
        print("  Anderen Port waehlen: keyworld serve --port 9000")
        return 1

    url = "http://127.0.0.1:%d/" % bound
    print("  Keyworld laeuft auf %s" % url)
    print("  Markt: %s   Beenden: Ctrl-C" % Config.market())
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  beendet")
    finally:
        srv.server_close()
    return 0
