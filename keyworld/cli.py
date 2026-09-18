"""Keyworld CLI -- Datenquellen-Schicht.

    keyworld probe                     Verbindung + Guthaben pruefen (kostenlos)
    keyworld discover <seed>           Layer 0, kostenlose Kandidaten
    keyworld scout <seed> [--go]       Layer 0+1, ohne --go nur Kostenschaetzung
    keyworld costs [--run ID]          Ausgabenuebersicht
"""
import argparse
import json
import sys

from .config import Config
from .render import (BOLD, DIM, RST, W, bar, cost_report, head, money, rule, table)
from .store import Run, all_runs, lifetime_cost


def cmd_probe(args):
    print(head("PROBE  markt=%s" % Config.market()))
    if not Config.has_dataforseo():
        print("  %sDataForSEO: keine Credentials%s" % (DIM, RST))
        print("  -> cp .env.example .env  und Login/Passwort eintragen")
        print("  -> Layer 0 (Discovery) laeuft auch ohne Credentials.")
        return 1
    from .providers import DataForSEO
    ok, msg = DataForSEO().probe()
    print("  DataForSEO: %s %s" % ("OK" if ok else "FEHLER", msg))
    spent, saved, calls = lifetime_cost()
    print("  Keyworld lifetime: $%.4f ueber %d Calls (gespart: $%.4f)" % (spent, calls, saved))
    return 0 if ok else 1


def cmd_discover(args):
    from .providers import FreeDiscovery
    with Run(seed=args.seed, budget_usd=0.0) as run:
        kws = FreeDiscovery().expand(args.seed, run, limit=args.limit, deep=args.deep)
        print(head("DISCOVERY  seed=%s  layer=0  kosten=$0.0000" % args.seed))
        for k in kws:
            print("  " + k)
        print(rule("-"))
        print("  %d Kandidaten | naechster Schritt: keyworld scout %s" % (len(kws), args.seed))
        if args.json:
            open(args.json, "w").write(json.dumps(kws, ensure_ascii=False, indent=2))
            print("  -> %s" % args.json)
    return 0


def _mix_str(mix):
    """Kompakte SERP-Zusammensetzung: 4P 5E 1M -> Produkt, Editorial, Marktplatz."""
    short = {"product": "P", "editorial": "E", "marketplace": "M",
             "ugc": "U", "aggregator": "A"}
    return " ".join("%d%s" % (mix[k], short[k]) for k in
                    ("product", "editorial", "aggregator", "ugc", "marketplace")
                    if mix.get(k))


def cmd_scout(args):
    from .filters import Funnel, annotate_intent, order
    from .providers import DataForSEO, FreeDiscovery
    from .serp import analyse, find_incumbents
    if not Config.has_dataforseo():
        print("  Keine DataForSEO-Credentials -- siehe .env.example", file=sys.stderr)
        return 1

    seeds = list(args.seed) if isinstance(args.seed, list) else [args.seed]
    if args.seeds_file:
        extra = [l.strip() for l in open(args.seeds_file, encoding="utf-8")]
        seeds += [l for l in extra if l and not l.startswith("#")]
    seeds = list(dict.fromkeys(seeds))
    with Run(seed=", ".join(seeds), budget_usd=args.budget) as run:
        dfs = DataForSEO(live=args.live)
        disc = FreeDiscovery()

        candidates = []
        for sd in seeds:
            candidates += disc.expand(sd, run, limit=args.limit, deep=args.deep)
        candidates = list(dict.fromkeys(candidates))
        print(head("SCOUT  seed=%s  markt=%s" % (", ".join(seeds), Config.market()), W))
        print("  Layer 0: %d Kandidaten aus %d Seed(s) (kostenlos)" % (len(candidates), len(seeds)))

        est_sv, tasks = dfs.estimate_search_volume_cost(len(candidates))
        print("  Layer 1: %d Task(s) -> geschaetzt %s" % (tasks, money(est_sv)))
        if not args.go:
            print("  Layer 2: erst nach den Filtern bezifferbar (~%s je Keyword)"
                  % money(dfs.estimate_serp_cost(1)))
            print(rule("-", W))
            print("  %sTrockenlauf.%s Mit --go tatsaechlich abrufen." % (BOLD, RST))
            return 0

        metrics = dfs.search_volume(candidates, run)

        funnel = Funnel(args.min_volume, args.max_volume, args.min_cpc)
        survivors = funnel.run(metrics)
        print(rule("-", W))
        print(table(funnel.report_rows(), [("stufe", "FILTER", 26, "l"),
                                           ("raus", "RAUS", 6, "r"),
                                           ("uebrig", "UEBRIG", 7, "r")], W))
        if not survivors:
            print("\n  Nichts ueberlebt die Filter. Seed zu eng oder zu kommerziell.")
            print("\n" + cost_report(run, W))
            return 0

        if args.max_serp and len(survivors) > args.max_serp:
            survivors = sorted(survivors, key=lambda m: -(m.cpc or 0))[:args.max_serp]
            print("  (auf die %d Ueberlebenden mit dem hoechsten CPC begrenzt)" % args.max_serp)

        est_serp = dfs.estimate_serp_cost(len(survivors))
        print(rule("-", W))
        print("  Layer 2: SERP fuer %d Ueberlebende -> %s%s"
              % (len(survivors), money(est_serp), "  (live)" if args.live else "  (queue)"))
        serps = dfs.serp_batch([m.keyword for m in survivors], run)

        incumbents = find_incumbents(serps, args.incumbent_share)
        analysis = {k: analyse(v, incumbents) for k, v in serps.items() if v}
        analysis = {k: v for k, v in analysis.items() if v}
        print("  Platzhirsche im Set: %s" % (", ".join(sorted(incumbents)[:6]) or "keine"))

        def diff_of(kw):
            a = analysis.get(kw)
            return a["difficulty"] if a else None

        annotated = order(annotate_intent(survivors), diff_of)
        blocks = [("STUFE 1-2  Werkzeugnot und Wechsel", [x for x in annotated if x[1] <= 2]),
                  ("STUFE 3-4  Kaufabsicht und Orientierung", [x for x in annotated if x[1] > 2])]

        survivors = funnel.run_serp_stage(survivors, analysis, args.product_serp_only)
        if args.product_serp_only:
            print("  %d nach Aussortieren rein redaktioneller SERPs" % len(survivors))
            annotated = order(annotate_intent(survivors), diff_of)
            blocks = [("STUFE 1-2  Werkzeugnot und Wechsel",
                       [x for x in annotated if x[1] <= 2]),
                      ("STUFE 3-4  Kaufabsicht und Orientierung",
                       [x for x in annotated if x[1] > 2])]

        cols = [("kw", "KEYWORD", 38, "l"), ("lvl", "ST", 2, "r"),
                ("diff", "DIFF", 4, "r"), ("viz", "", 10, "l"),
                ("si", "SERP", 4, "l"), ("cpc", "CPC", 6, "r"),
                ("vol", "VOL", 6, "r"), ("mix", "SERP-MIX", 14, "l"),
                ("top", "PLATZHIRSCH", 18, "l")]
        for title, rows_in in blocks:
            print()
            print("%s%s%s" % (BOLD, title, RST))
            out = []
            for m, lvl, _n, _p in rows_in[:args.top]:
                a = analysis.get(m.keyword)
                inc = [d for d in (a or {}).get("top_domains", []) if d in incumbents]
                out.append({"kw": m.keyword, "lvl": lvl,
                            "diff": a["difficulty"] if a else None,
                            "viz": bar(a["difficulty"] if a else None, 100, 10),
                            "cpc": m.cpc, "vol": m.search_volume,
                            "si": (a or {}).get("serp_intent", ""),
                            "mix": _mix_str(a["mix"]) if a else "",
                            "top": inc[0] if inc else ((a or {}).get("top_domains") or [""])[0]})
            print(table(out, cols, W))

        # Gratis-Ertrag: verwandte Suchen und PAA kamen in den SERPs schon mit
        bonus = []
        for v in serps.values():
            bonus += v.get("related", []) + v.get("paa", [])
        bonus = [b for b in dict.fromkeys(x.lower().strip() for x in bonus)
                 if b not in {m.keyword for m in metrics}]
        if bonus:
            print()
            print("  %d neue Kandidaten aus verwandten Suchen und PAA (bereits bezahlt)" % len(bonus))
            print("  %s%s%s" % (DIM, ", ".join(bonus[:6]) + (" ..." if len(bonus) > 6 else ""), RST))

        print()
        print(cost_report(run, W))

        payload = [dict(m.to_dict(), intent_level=lvl, intent=name,
                        serp=analysis.get(m.keyword))
                   for m, lvl, name, _p in annotated]
        if args.json:
            open(args.json, "w").write(json.dumps(payload, ensure_ascii=False, indent=2))
            print("  -> %s" % args.json)
        if args.harvest and bonus:
            # Die Ernte des einen Laufs ist die Saat des naechsten
            with open(args.harvest, "w", encoding="utf-8") as fh:
                fh.write("\n".join(bonus) + "\n")
            print("  -> %s (%d Kandidaten fuer den naechsten Lauf)" % (args.harvest, len(bonus)))
        if args.report:
            from .report import write_report
            path = write_report(args.report, seeds, payload, funnel, run,
                                incumbents, bonus)
            print("  -> %s" % path)
    return 0


def cmd_fetch(args):
    """Sammelt bezahlte, aber nicht abgeholte Queue-Tasks ein. Kostenlos."""
    from .providers import DataForSEO
    from .store import open_tasks
    tasks = open_tasks()
    print(head("FETCH  %d offene Task(s)" % len(tasks)))
    if not tasks:
        print("  %sNichts abzuholen.%s" % (DIM, RST))
        return 0
    dfs = DataForSEO()
    with Run(seed="fetch", budget_usd=0.0) as run:
        total = 0
        for t in tasks:
            metrics, done = dfs.collect(t, run)
            total += len(metrics)
            print("  %s  %-28s %5d Keywords  %s (bezahlt: $%.4f)"
                  % (t["task_id"][:18], t["path_base"].split("/")[-1], len(metrics),
                     "OK" if done else "noch in Queue", t["cost_usd"]))
        print(rule("-"))
        print("  %d Keywords in den Cache geholt fuer $0.0000" % total)
    return 0


def cmd_costs(args):
    if args.run:
        conn = Run.__new__(Run)
        from .store import connect
        conn.conn, conn.run_id, conn.seed = connect(), args.run, ""
        conn.budget = Config.run_budget_usd
        print(cost_report(conn))
        return 0
    print(head("RUNS"))
    rows = [{"run_id": r["run_id"], "seed": r["seed"], "market": r["market"],
             "calls": r["calls"], "cost": r["cost"], "status": r["status"]}
            for r in all_runs(args.limit)]
    print(table(rows, [("run_id", "RUN", 13, "l"), ("seed", "SEED", 22, "l"),
                       ("market", "MARKT", 9, "l"), ("calls", "CALLS", 5, "r"),
                       ("cost", "USD", 8, "r"), ("status", "STATUS", 7, "l")]))
    spent, saved, calls = lifetime_cost()
    print(rule("-"))
    print("  Gesamt: %s ueber %d Calls | durch Cache gespart: $%.4f" % (money(spent), calls, saved))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="keyworld", description="Keyword-Opportunity-Scouting")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("probe", help="Verbindung und Guthaben pruefen").set_defaults(fn=cmd_probe)

    d = sub.add_parser("discover", help="Layer 0: kostenlose Kandidaten")
    d.add_argument("seed")
    d.add_argument("--limit", type=int, default=500)
    d.add_argument("--deep", action="store_true", help="zusaetzlich a-z Alphabet-Suffixe")
    d.add_argument("--json", help="Ergebnis als JSON speichern")
    d.set_defaults(fn=cmd_discover)

    s = sub.add_parser("scout", help="Vollstaendiger Lauf: Discovery, Metriken, SERP")
    s.add_argument("seed", nargs="*", default=[], help="ein oder mehrere Seeds")
    s.add_argument("--seeds-file", help="Seeds aus Datei, eine je Zeile")
    s.add_argument("--harvest", help="verwandte Suchen und PAA als Seed-Datei schreiben")
    s.add_argument("--go", action="store_true", help="wirklich abrufen (sonst Trockenlauf)")
    s.add_argument("--limit", type=int, default=500)
    s.add_argument("--top", type=int, default=40)
    s.add_argument("--min-volume", type=int, default=300,
                   help="darunter raus (Default 300)")
    s.add_argument("--max-volume", type=int, default=10000,
                   help="darueber raus, dort sitzt schon jemand mit Budget")
    s.add_argument("--min-cpc", type=float, default=0.50,
                   help="darunter raus, Informationssuche")
    s.add_argument("--budget", type=float, default=Config.run_budget_usd)
    s.add_argument("--live", action="store_true",
                   help="Live-Modus: schneller, aber gut 3x teurer")
    s.add_argument("--max-serp", type=int, default=120,
                   help="Deckel fuer Layer 2, nimmt die hoechsten CPCs")
    s.add_argument("--incumbent-share", type=float, default=0.25,
                   help="ab welchem Anteil der SERPs eine Domain als Platzhirsch gilt")
    s.add_argument("--product-serp-only", action="store_true",
                   help="SERPs ohne jede Produktseite aussortieren (RED)")
    s.add_argument("--report", help="HTML-Report schreiben")
    s.add_argument("--deep", action="store_true")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_scout)

    f = sub.add_parser("fetch", help="Offene Queue-Tasks einsammeln (kostenlos)")
    f.set_defaults(fn=cmd_fetch)

    c = sub.add_parser("costs", help="Ausgabenuebersicht")
    c.add_argument("--run", help="Detailansicht fuer eine Run-ID")
    c.add_argument("--limit", type=int, default=20)
    c.set_defaults(fn=cmd_costs)

    args = p.parse_args(argv)
    if getattr(args, "cmd", None) == "scout" and not args.seed and not args.seeds_file:
        p.error("scout braucht mindestens einen Seed oder --seeds-file")
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
