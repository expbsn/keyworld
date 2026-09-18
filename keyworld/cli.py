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
from .render import (BOLD, DIM, RST, bar, cost_report, head, money, rule, table)
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


def cmd_scout(args):
    from .filters import Funnel, annotate_intent, order
    from .providers import DataForSEO, FreeDiscovery
    if not Config.has_dataforseo():
        print("  Keine DataForSEO-Credentials -- siehe .env.example", file=sys.stderr)
        return 1

    with Run(seed=args.seed, budget_usd=args.budget) as run:
        dfs = DataForSEO(live=not args.queued)
        candidates = FreeDiscovery().expand(args.seed, run, limit=args.limit, deep=args.deep)
        print(head("SCOUT  seed=%s  markt=%s" % (args.seed, Config.market())))
        print("  Layer 0: %d Kandidaten (kostenlos)" % len(candidates))

        est_sv, tasks = dfs.estimate_search_volume_cost(len(candidates))
        print("  Layer 1: %d Task(s) -> geschaetzt %s" % (tasks, money(est_sv)))
        if not args.go:
            print("  Layer 2: erst nach den Filtern bezifferbar")
            print(rule("-"))
            print("  %sTrockenlauf.%s Mit --go tatsaechlich abrufen." % (BOLD, RST))
            return 0

        metrics = dfs.search_volume(candidates, run)

        # -- Filterkette: hart, in fester Reihenfolge, jeder Abgang sichtbar
        funnel = Funnel(args.min_volume, args.max_volume, args.min_cpc)
        survivors = funnel.run(metrics)
        print(rule("-"))
        print(table(funnel.report_rows(), [("stufe", "FILTER", 26, "l"),
                                           ("raus", "RAUS", 6, "r"),
                                           ("uebrig", "UEBRIG", 7, "r")]))
        if not survivors:
            print("\n  Nichts ueberlebt die Filter. Seed zu eng oder zu kommerziell.")
            print()
            print(cost_report(run))
            return 0

        # -- Layer 2 nur auf Ueberlebende
        est_kd, reqs = dfs.estimate_difficulty_cost(len(survivors))
        print(rule("-"))
        print("  Layer 2: Difficulty fuer %d Ueberlebende -> %s" % (len(survivors), money(est_kd)))
        kd = dfs.keyword_difficulty([m.keyword for m in survivors], run)

        annotated = order(annotate_intent(survivors), lambda k: kd.get(k))
        blocks = [("STUFE 1-2  Werkzeugnot und Wechsel", [a for a in annotated if a[1] <= 2]),
                  ("STUFE 3-4  Kaufabsicht und Orientierung", [a for a in annotated if a[1] > 2])]

        cols = [("kw", "KEYWORD", 36, "l"), ("lvl", "ST", 2, "r"),
                ("kd", "DIFF", 4, "r"), ("cpc", "CPC", 6, "r"),
                ("vol", "VOL", 6, "r"), ("viz", "DIFFICULTY", 12, "l")]
        for title, rows_in in blocks:
            print()
            print("%s%s%s" % (BOLD, title, RST))
            print(table([{"kw": m.keyword, "lvl": lvl, "kd": kd.get(m.keyword),
                          "cpc": m.cpc, "vol": m.search_volume,
                          "viz": bar(kd.get(m.keyword), 100, 12)}
                         for m, lvl, _n, _p in rows_in[:args.top]], cols))

        print()
        print(cost_report(run))
        if args.json:
            payload = [dict(m.to_dict(), intent_level=lvl, intent=name,
                            keyword_difficulty=kd.get(m.keyword))
                       for m, lvl, name, _p in annotated]
            open(args.json, "w").write(json.dumps(payload, ensure_ascii=False, indent=2))
            print("  -> %s" % args.json)
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

    s = sub.add_parser("scout", help="Layer 0+1: Kandidaten mit Volumen und CPC")
    s.add_argument("seed")
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
    s.add_argument("--queued", action="store_true", help="Standard-Queue $0.06 statt Live $0.09")
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
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
