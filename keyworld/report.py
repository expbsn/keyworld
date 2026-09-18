"""HTML-Report: eine Datei, keine Abhaengigkeiten, Mono.

Gleicher Inhalt wie die Terminal-Ausgabe, nur sortierbar und mit Balken.
Bewusst kein Framework -- der Report soll in fuenf Jahren noch aufgehen.
"""
import html
import json
from datetime import datetime
from pathlib import Path

CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--dim:#8a8a82;--line:#e0e0da;--card:#fff;
--acc:#2f6f4f;--warn:#9a6b1f;--bad:#9a3b2f;--bar:#d8d8d0}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#111110;--fg:#e8e8e2;--dim:#7a7a72;--line:#2a2a27;--card:#191918;
--acc:#6fbf8f;--warn:#d9a441;--bad:#e07a6a;--bar:#2e2e2a}}
:root[data-theme=dark]{--bg:#111110;--fg:#e8e8e2;--dim:#7a7a72;--line:#2a2a27;
--card:#191918;--acc:#6fbf8f;--warn:#d9a441;--bad:#e07a6a;--bar:#2e2e2a}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);
font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.wrap{max-width:1180px;margin:0 auto}
.tblwrap{overflow-x:auto}
h1{font-size:16px;margin:0 0 2px;letter-spacing:.02em}
h2{font-size:13px;margin:30px 0 8px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim)}
.sub{color:var(--dim);margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:8px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:5px;padding:11px 13px}
.kpi b{display:block;font-size:18px;font-weight:600;letter-spacing:-.01em}
.kpi span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:5px;overflow:hidden}
table.kw{table-layout:fixed;min-width:820px}
table.kw th:nth-child(1),table.kw td:nth-child(1){width:30%;overflow:hidden;text-overflow:ellipsis}
table.kw th:nth-child(2),table.kw td:nth-child(2){width:44px}
table.kw th:nth-child(3),table.kw td:nth-child(3){width:118px}
table.kw th:nth-child(4),table.kw td:nth-child(4){width:64px}
table.kw th:nth-child(5),table.kw td:nth-child(5){width:64px}
table.kw th:nth-child(6),table.kw td:nth-child(6){width:64px}
table.kw th:nth-child(7),table.kw td:nth-child(7){width:110px}
table.kw th:nth-child(8),table.kw td:nth-child(8){overflow:hidden;text-overflow:ellipsis}
th,td{padding:6px 9px;text-align:left;border-bottom:1px solid var(--line);
white-space:nowrap;font-variant-numeric:tabular-nums}
th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;
letter-spacing:.05em;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--card)}
th:hover{color:var(--fg)}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:color-mix(in srgb,var(--fg) 4%,transparent)}
.n{text-align:right}
.bar{display:inline-block;height:7px;border-radius:2px;background:var(--bar);vertical-align:middle;min-width:1px}
.bar>i{display:block;height:100%;border-radius:2px;background:var(--acc)}
.b-mid>i{background:var(--warn)}.b-hi>i{background:var(--bad)}
.lvl{display:inline-block;width:17px;height:17px;line-height:17px;text-align:center;
border-radius:3px;font-size:11px;background:var(--bar)}
.lvl-1,.lvl-2{background:var(--acc);color:var(--bg);font-weight:600}
.dim{color:var(--dim)}
.si{font-size:10px;letter-spacing:.06em;padding:1px 5px;border-radius:3px;border:1px solid var(--line)}
.si-PROD{color:var(--acc);border-color:var(--acc)}
.si-RED{color:var(--bad);border-color:var(--bad)}
.tags{margin:6px 0 0;color:var(--dim);line-height:1.9;white-space:normal}
.tag{border:1px solid var(--line);border-radius:3px;padding:1px 6px;margin-right:4px}
footer{margin-top:34px;color:var(--dim);border-top:1px solid var(--line);padding-top:12px}
@media(max-width:640px){body{padding:16px 12px 48px}}
"""

JS = """
document.querySelectorAll('table').forEach(function(t){
 t.querySelectorAll('th').forEach(function(th,i){
  th.onclick=function(){
   var tb=t.tBodies[0],rows=[].slice.call(tb.rows),
       asc=th.dataset.asc!=='1';
   t.querySelectorAll('th').forEach(function(o){o.dataset.asc=''});
   th.dataset.asc=asc?'1':'0';
   rows.sort(function(a,b){
    var x=a.cells[i].dataset.v??a.cells[i].textContent,
        y=b.cells[i].dataset.v??b.cells[i].textContent,
        nx=parseFloat(x),ny=parseFloat(y);
    if(!isNaN(nx)&&!isNaN(ny))return asc?nx-ny:ny-nx;
    return asc?String(x).localeCompare(String(y)):String(y).localeCompare(String(x));
   });
   rows.forEach(function(r){tb.appendChild(r)});
  };
 });
});
"""


def _e(v):
    return html.escape("" if v is None else str(v))


def _bar(v, vmax=100):
    if v is None:
        return '<span class="dim">?</span>'
    pct = max(2, min(100, round(100.0 * v / vmax)))
    cls = "bar" + (" b-hi" if v >= 60 else " b-mid" if v >= 35 else "")
    return ('<span class="%s" style="width:54px"><i style="width:%d%%"></i></span> %s'
            % (cls, pct, v))


def _mix(mix):
    short = {"product": "P", "editorial": "E", "aggregator": "A",
             "ugc": "U", "marketplace": "M"}
    return " ".join("%d%s" % (mix[k], s) for k, s in short.items() if mix.get(k))


def _rows(items, incumbents):
    out = []
    for r in items:
        s = r.get("serp") or {}
        d = s.get("difficulty")
        inc = [x for x in s.get("top_domains", []) if x in incumbents]
        top = inc[0] if inc else (s.get("top_domains") or [""])[0]
        out.append(
            "<tr>"
            '<td>%s</td>'
            '<td class="n" data-v="%s"><span class="lvl lvl-%s">%s</span></td>'
            '<td class="n" data-v="%s">%s</td>'
            '<td class="n" data-v="%s">%s</td>'
            '<td class="n" data-v="%s">%s</td>'
            '<td>%s</td>'
            '<td class="dim">%s</td>'
            '<td class="%s">%s</td>'
            "</tr>" % (
                _e(r["keyword"]), r["intent_level"], r["intent_level"], r["intent_level"],
                "" if d is None else d, _bar(d),
                r.get("cpc") or 0, ("%.2f" % r["cpc"]) if r.get("cpc") else "-",
                r.get("search_volume") or 0, r.get("search_volume") or "-",
                '<span class="si si-%s">%s</span>' % (s.get("serp_intent", ""),
                                                      s.get("serp_intent", "-")),
                _e(_mix(s.get("mix", {}))),
                "dim" if top not in incumbents else "", _e(top)))
    return "\n".join(out)


TABLE_HEAD = ("<thead><tr><th>Keyword</th><th>St</th><th>Difficulty</th>"
              "<th>CPC</th><th>Vol</th><th>SERP</th><th>SERP-Mix</th><th>Platzhirsch</th>"
              "</tr></thead>")


def write_report(path, seeds, items, funnel, run, incumbents, bonus):
    a = [r for r in items if r["intent_level"] <= 2]
    b = [r for r in items if r["intent_level"] > 2]
    known = [r for r in items if (r.get("serp") or {}).get("difficulty") is not None]
    easiest = min((r["serp"]["difficulty"] for r in known), default=None)

    funnel_rows = "\n".join(
        '<tr><td>%s</td><td class="n">%s</td><td class="n">%s</td></tr>'
        % (_e(k), v["raus"] or "", v["uebrig"]) for k, v in funnel.stages.items())
    ledger_rows = "\n".join(
        '<tr><td>%s</td><td>%s</td><td>%s</td><td class="n">%s</td>'
        '<td class="n">%.4f</td><td class="n dim">%.4f</td></tr>'
        % (_e(r["layer"]), _e(r["provider"]), _e(r["endpoint"]), r["units"],
           r["cost"], r["saved"] or 0) for r in run.breakdown())

    doc = """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Keyworld %(seed)s</title><style>%(css)s</style>
<div class="wrap">
<h1>KEYWORLD &middot; %(seed)s</h1>
<div class="sub">%(market)s &middot; %(date)s &middot; run %(run)s</div>

<div class="grid">
 <div class="kpi"><span>Ueberlebende</span><b>%(n)d</b></div>
 <div class="kpi"><span>Stufe 1-2</span><b>%(na)d</b></div>
 <div class="kpi"><span>Leichtestes</span><b>%(easy)s</b></div>
 <div class="kpi"><span>Ausgegeben</span><b>$%(cost).4f</b></div>
 <div class="kpi"><span>Gespart (Cache)</span><b>$%(saved).4f</b></div>
</div>

<h2>Filter-Trichter</h2>
<div class="tblwrap"><table><thead><tr><th>Filter</th><th>Raus</th><th>Uebrig</th></tr></thead>
<tbody>%(funnel)s</tbody></table></div>

<h2>Stufe 1-2 &middot; Werkzeugnot und Wechsel</h2>
<div class="tblwrap"><table class="kw">%(th)s<tbody>%(rows_a)s</tbody></table></div>

<h2>Stufe 3-4 &middot; Kaufabsicht und Orientierung</h2>
<div class="tblwrap"><table class="kw">%(th)s<tbody>%(rows_b)s</tbody></table></div>

<h2>Platzhirsche</h2>
<p class="tags">%(inc)s</p>

<h2>Neue Kandidaten aus verwandten Suchen (bereits bezahlt)</h2>
<p class="tags">%(bonus)s</p>

<h2>Ausgaben</h2>
<div class="tblwrap"><table><thead><tr><th>Layer</th><th>Provider</th><th>Endpoint</th>
<th>Units</th><th>USD</th><th>Gespart</th></tr></thead>
<tbody>%(ledger)s</tbody></table></div>

<footer>Difficulty ist aus der SERP-Zusammensetzung abgeleitet, nicht gekauft:
Anteil echter Produktseiten in den Top&nbsp;10, Praesenz der Platzhirsche und
Domain-Vielfalt. Niedrig heisst angreifbar. SERP-Mix: P&nbsp;Produkt,
E&nbsp;Editorial, A&nbsp;Aggregator, U&nbsp;UGC, M&nbsp;Marktplatz.
SERP&nbsp;PROD heisst, Google zeigt dort echte Produktseiten; RED heisst rein
redaktionell &ndash; eine niedrige Difficulty ist dort kein Einstieg, sondern
die falsche Suchabsicht.</footer>
</div><script>%(js)s</script>""" % {
        "css": CSS, "js": JS, "seed": _e(", ".join(seeds)),
        "market": _e(run.conn.execute("SELECT market FROM runs WHERE run_id=?",
                                      (run.run_id,)).fetchone()["market"]),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "run": _e(run.run_id),
        "n": len(items), "na": len(a),
        "easy": "-" if easiest is None else str(easiest),
        "cost": run.total_cost(), "saved": run.total_saved(),
        "funnel": funnel_rows, "th": TABLE_HEAD,
        "rows_a": _rows(a, incumbents) or '<tr><td class="dim">keine</td></tr>',
        "rows_b": _rows(b, incumbents) or '<tr><td class="dim">keine</td></tr>',
        "inc": " ".join('<span class="tag">%s</span>' % _e(d) for d in sorted(incumbents)) or "keine",
        "bonus": " ".join('<span class="tag">%s</span>' % _e(x) for x in bonus[:40]) or "keine",
        "ledger": ledger_rows,
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return str(out)
