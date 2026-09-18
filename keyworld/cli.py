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
    from .providers import DataForSEO, FreeDiscovery
    if not Config.has_dataforseo():
        print("  Keine DataForSEO-Credentials -- siehe .env.example", file=sys.stderr)
        return 1

    with Run(seed=args.seed, budget_usd=args.budget) as run:
        dfs = DataForSEO(live=not args.queued)
        candidates = FreeDiscovery().expand(args.seed, run, limit=args.limit, deep=args.deep)
        print(head("SCOUT  seed=%s  markt=%s" % (args.seed, Config.market())))
        print("  Layer 0: %d Kandidaten (kostenlos)" % len(candidates))

        est, tasks = dfs.estimate_search_volume_cost(len(candidates))
        print("  Layer 1: %d Tasks a %d Keywords -> geschaetzt %s"
              % (tasks, min(len(candidates), 1000), money(est)))
        if not args.go:
            print(rule("-"))
            print("  %sTrockenlauf.%s Mit --go tatsaechlich abrufen." % (BOLD, RST))
            return 0

        metrics = dfs.search_volume(candidates, run)
        metrics = [m for m in metrics if (m.search_volume or 0) >= args.min_volume]
        metrics.sort(key=lambda m: (m.search_volume or 0), reverse=True)

        vmax = max([m.search_volume or 0 for m in metrics] or [1])
        rows = [{
            "kw": m.keyword,
            "vol": m.search_volume,
            "cpc": m.cpc,
            "comp": m.competition_index,
            "trend": m.trend_ratio(),
            "viz": bar(m.search_volume, vmax, 12),
        } for m in metrics[:args.top]]
        print(rule("-"))
        print(table(rows, [("kw", "KEYWORD", 34, "l"), ("vol", "VOL", 7, "r"),
                           ("cpc", "CPC", 7, "r"), ("comp", "COMP", 4, "r"),
                           ("trend", "TREND", 6, "r"), ("viz", "VOLUMEN", 12, "l")]))
        print()
        print(cost_report(run))
        if args.json:
            open(args.json, "w").write(json.dumps([m.to_dict() for m in metrics],
                                                  ensure_ascii=False, indent=2))
            print("  -> %s" % args.json)
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
    s.add_argument("--min-volume", type=int, default=10)
    s.add_argument("--budget", type=float, default=Config.run_budget_usd)
    s.add_argument("--queued", action="store_true", help="Standard-Queue $0.06 statt Live $0.09")
    s.add_argument("--deep", action="store_true")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_scout)

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
