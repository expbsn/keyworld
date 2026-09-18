"""Lokales Interface. Stdlib-HTTP, keine Abhaengigkeit, kein Framework.

Duenne Schicht ueber denselben Funktionen, die die CLI nutzt -- es gibt
keine Logik, die nur hier existiert. Das Interface liest den
Zustandsspeicher und schreibt genau zwei Dinge zurueck: deinen Zustand je
Keyword und deine Korrekturen in overrides.csv.

Es scoutet nicht. Abrufen kostet Geld, und Geld gibt man auf der
Kommandozeile aus, nicht per Klick im Browser.
"""
import json
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .classify import CONFIDENCE_FLOOR, KINDS, add_override, load_overrides
from .config import Config
from .store import (all_runs, connect, keyword_rows, lifetime_cost,
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

    def do_GET(self):
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
        if u.path == "/api/costs":
            return self._send(200, costs())
        return self._send(404, {"error": "not found"})

    def do_POST(self):
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

        if u.path == "/api/override":
            dom = (body.get("domain") or "").strip().lower()
            kind = (body.get("type") or "").strip().lower()
            if not dom or kind not in KINDS:
                return self._send(400, {"error": "domain und type noetig"})
            add_override(dom, kind, body.get("note") or "")
            return self._send(200, {"ok": True, "overrides": len(load_overrides())})

        return self._send(404, {"error": "not found"})


def serve(port=8765, open_browser=True):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
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
