"""Keyworld CLI.

    keyworld probe                      Verbindung und Guthaben, kostenlos
    keyworld gen                        Themengebiete x Muster -> Keywords
    keyworld scout                      Kompletter Lauf -> CSV-Shortlist
    keyworld review                     Zustand und Notiz je Keyword
    keyworld queue                      Unsichere Domains fuer overrides.csv
    keyworld costs                      Ausgabenuebersicht
    keyworld fetch                      Offene Queue-Tasks einsammeln
"""
import argparse
import json
import re
import sys

from .config import Config
from .render import BOLD, DIM, RST, W, bar, cost_report, head, money, rule, table
from .store import Run, all_runs, keyword_rows, lifetime_cost, state_counts

STATES = ("new", "shortlisted", "reviewed", "rejected")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "run"


# -- probe --------------------------------------------------------------------
def cmd_probe(args):
    print(head("PROBE  markt=%s" % Config.market()))
    if not Config.has_dataforseo():
        print("  %sDataForSEO: keine Credentials%s" % (DIM, RST))
        print("  -> cp .env.example .env  und Login/API-Passwort eintragen")
        return 1
    from .providers import DataForSEO
    ok, msg = DataForSEO().probe()
    print("  DataForSEO: %s %s" % ("OK" if ok else "FEHLER", msg))
    spent, saved, calls = lifetime_cost()
    print("  Keyworld lifetime: $%.4f ueber %d Calls (gespart: $%.4f)" % (spent, calls, saved))
    counts = state_counts(Config.market())
    if counts:
        print("  Keywords: " + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
    return 0


# -- gen ----------------------------------------------------------------------
def cmd_gen(args):
    from .generate import expand, read_list
    from .intent import classify
    domains = list(args.domain) + (read_list(args.domains) if args.domains else [])
    patterns = read_list(args.patterns) if args.patterns else read_list("data/patterns.txt")
    if not domains:
        print("  Keine Themengebiete. --domain oder --domains angeben.", file=sys.stderr)
        return 1
    kws = expand(domains, patterns)
    from collections import Counter
    dist = Counter(classify(k)[0] for k in kws)
    print(head("GEN  %d Themengebiete x %d Muster = %d Keywords"
               % (len(domains), len(patterns), len(kws))))
    print("  Stufen: " + "  ".join("%d=%d" % (t, dist[t]) for t in sorted(dist)))
    if args.out:
        open(args.out, "w", encoding="utf-8").write("\n".join(kws) + "\n")
        print("  -> %s" % args.out)
    else:
        for k in kws:
            print("  " + k)
    return 0


# -- discover -----------------------------------------------------------------
def cmd_discover(args):
    from .providers import FreeDiscovery
    with Run(seed=args.seed, budget_usd=0.0) as run:
        kws = FreeDiscovery().expand(args.seed, run, limit=args.limit, deep=args.deep)
        print(head("DISCOVERY  seed=%s  kosten=$0.0000" % args.seed))
        for k in kws:
            print("  " + k)
        print(rule("-"))
        print("  %d Kandidaten" % len(kws))
        if args.out:
            open(args.out, "w", encoding="utf-8").write("\n".join(kws) + "\n")
            print("  -> %s" % args.out)
    return 0


# -- scout --------------------------------------------------------------------
def _collect_candidates(args, run):
    """Kartesisches Produkt, Seed-Datei und Autocomplete zusammenfuehren."""
    from .generate import expand, read_list
    out = []
    domains = list(args.domain) + (read_list(args.domains) if args.domains else [])
    if domains:
        patterns = read_list(args.patterns or "data/patterns.txt")
        out += expand(domains, patterns)
        print("  Generator: %d Themengebiete x %d Muster = %d Keywords"
              % (len(domains), len(patterns), len(out)))
    if args.keywords_file:
        extra = read_list(args.keywords_file)
        out += extra
        print("  Datei: %d Keywords" % len(extra))
    if args.seed:
        from .providers import FreeDiscovery
        disc = FreeDiscovery()
        n0 = len(out)
        for sd in args.seed:
            out += disc.expand(sd, run, limit=args.limit, deep=args.deep)
        print("  Autocomplete: %d Kandidaten aus %d Seed(s)" % (len(out) - n0, len(args.seed)))
    return list(dict.fromkeys(k.strip().lower() for k in out if k.strip()))


def cmd_scout(args):
    from .classify import CONFIDENCE_FLOOR, load_overrides
    from .export import write_csv, write_review_csv
    from .filters import Funnel, annotate_intent, order, split_tiers
    from .providers import DataForSEO
    from .serp import analyse, attach_verdicts, find_incumbents, review_queue

    if not Config.has_dataforseo():
        print("  Keine DataForSEO-Credentials -- siehe .env.example", file=sys.stderr)
        return 1
    market = Config.market()
    label = ", ".join(args.domain or args.seed or ["lauf"])

    with Run(seed=label, budget_usd=args.budget) as run:
        dfs = DataForSEO(live=args.live)
        print(head("SCOUT  %s  markt=%s" % (label, market)))
        candidates = _collect_candidates(args, run)
        if not candidates:
            print("  Keine Kandidaten.", file=sys.stderr)
            return 1

        known = run.states(market, candidates)
        if not args.include_rejected:
            dropped = [k for k in candidates if known.get(k, {}).get("state") == "rejected"]
            candidates = [k for k in candidates if k not in set(dropped)]
            if dropped:
                print("  %d bereits abgelehnt, uebersprungen" % len(dropped))
        print("  %d Kandidaten gesamt" % len(candidates))

        est_sv, tasks = dfs.estimate_search_volume_cost(len(candidates))
        print("  Layer 1: %d Task(s) -> %s" % (tasks, money(est_sv)))
        if not args.go:
            print("  Layer 2: erst nach den Filtern bezifferbar (%s je Keyword)"
                  % money(dfs.estimate_serp_cost(1)))
            print(rule("-"))
            print("  %sTrockenlauf.%s Mit --go tatsaechlich abrufen." % (BOLD, RST))
            return 0

        metrics = dfs.search_volume(candidates, run)

        funnel = Funnel(args.min_volume, args.max_volume, args.min_cpc)
        survivors = funnel.run(metrics)
        print(rule("-"))
        print(table(funnel.report_rows(), [("stufe", "FILTER", 26, "l"),
                                           ("raus", "RAUS", 6, "r"),
                                           ("uebrig", "UEBRIG", 7, "r")]))
        if not survivors:
            print("\n  Nichts ueberlebt die Filter.\n\n" + cost_report(run))
            return 0
        if args.max_serp and len(survivors) > args.max_serp:
            survivors = sorted(survivors, key=lambda m: -(m.cpc or 0))[:args.max_serp]
            print("  auf die %d mit dem hoechsten CPC begrenzt" % args.max_serp)

        print(rule("-"))
        print("  Layer 2: SERP fuer %d Ueberlebende -> %s"
              % (len(survivors), money(dfs.estimate_serp_cost(len(survivors)))))
        serps = dfs.serp_batch([m.keyword for m in survivors], run)

        # -- Seiten klassifizieren (kostet nichts ausser Zeit)
        overrides = load_overrides()
        targets, seen = [], set()
        for s in serps.values():
            for o in s["organics"]:
                if o["url"] and o["url"] not in seen:
                    seen.add(o["url"])
                    targets.append({"domain": o["domain"], "url": o["url"]})
        print("  Klassifikation: %d Seiten, %d Overrides aktiv"
              % (len(targets), len(overrides)))
        verdicts = _classify_cached(targets, overrides, run, args.workers)

        for s in serps.values():
            attach_verdicts(s, verdicts)
        incumbents = find_incumbents(serps, args.incumbent_share)
        analysis = dict((k, analyse(v, incumbents)) for k, v in serps.items() if v)
        analysis = dict((k, v) for k, v in analysis.items() if v)
        n_thin = sum(1 for v in analysis.values() if v["difficulty"] is None)
        print("  Platzhirsche: %s" % (", ".join(sorted(incumbents)[:6]) or "keine"))
        if n_thin:
            print("  %s%d Keywords ohne belastbare Difficulty -- werden zuerst gezeigt%s"
                  % (DIM, n_thin, RST))

        annotated = order(annotate_intent(survivors), lambda k: (analysis.get(k) or {}).get("difficulty"))
        visible, sunk = split_tiers(annotated)

        rows = []
        for m, lvl, name, _pat in annotated:
            a = analysis.get(m.keyword) or {}
            st = run.upsert_keyword(m.keyword, market, tier=lvl,
                                    difficulty=a.get("difficulty"), cpc=m.cpc,
                                    volume=m.search_volume, flags=" ".join(a.get("flags", [])))
            prev = known.get(m.keyword, {})
            rows.append(dict(m.to_dict(), tier=lvl, tier_name=name, serp=a or None,
                             state=prev.get("state", st), note=prev.get("note", ""),
                             market=market))
        run.commit()

        cols = [("kw", "KEYWORD", 34, "l"), ("tier", "T", 1, "r"),
                ("diff", "DIFF", 4, "r"), ("viz", "", 9, "l"),
                ("si", "SERP", 4, "l"), ("sure", "SICH", 5, "r"),
                ("flags", "SIGNALE", 20, "l"), ("cpc", "CPC", 6, "r"),
                ("vol", "VOL", 6, "r"), ("top", "TOP-DOMAIN", 20, "l")]
        for title, group in (("STUFE 1-3 UND OFFEN", visible), ("STUFE 4  Orientierung", sunk)):
            if not group:
                continue
            print()
            print("%s%s%s" % (BOLD, title, RST))
            print(table([_row(m, lvl, analysis, incumbents)
                         for m, lvl, _n, _p in group[:args.top]], cols))

        bonus = []
        for v in serps.values():
            bonus += v.get("related", []) + v.get("paa", [])
        bonus = [b for b in dict.fromkeys(x.lower().strip() for x in bonus)
                 if b not in set(m.keyword for m in metrics)]

        queue = review_queue(analysis, serps)
        print()
        print(cost_report(run))

        slug = _slug(label)
        csv_path = write_csv(args.csv or ("reports/%s.csv" % slug), rows)
        print("  -> %s  (%d Zeilen)" % (csv_path, len(rows)))
        if queue:
            q_path = write_review_csv(args.review_csv or ("reports/%s-pruefliste.csv" % slug), queue)
            print("  -> %s  (%d unsichere Domains, Format von overrides.csv)"
                  % (q_path, len(queue)))
        if bonus and args.harvest:
            open(args.harvest, "w", encoding="utf-8").write("\n".join(bonus) + "\n")
            print("  -> %s  (%d Kandidaten fuer den naechsten Lauf)" % (args.harvest, len(bonus)))
        if args.json:
            open(args.json, "w", encoding="utf-8").write(
                json.dumps(rows, ensure_ascii=False, indent=2))
            print("  -> %s" % args.json)
        if args.report:
            from .report import write_report
            print("  -> %s" % write_report(args.report, [label], rows, funnel, run,
                                           incumbents, bonus))
    return 0


def _classify_cached(targets, overrides, run, workers):
    """Klassifikation mit Cache -- ein zweiter Lauf ruft keine Seite erneut ab."""
    from .classify import classify_many
    cache = {}
    for t in targets:
        hit = run.cache_get("page", t["url"], ttl_days=30)
        if hit is not None:
            cache[t["url"]] = hit
    out = classify_many(targets, overrides, workers=workers, cache=cache)
    for url, v in out.items():
        # Overrides nicht in den Cache schreiben -- sie werden bei jedem Lauf
        # frisch aus der Datei gelesen und sollen dort aenderbar bleiben.
        if url not in cache and v.get("source") != "override":
            run.cache_put("page", url, v)
    run.commit()
    return out


def _row(m, lvl, analysis, incumbents):
    a = analysis.get(m.keyword) or {}
    d = a.get("difficulty")
    inc = [x for x in a.get("top_domains", []) if x in incumbents]
    return {"kw": m.keyword, "tier": lvl, "diff": d, "viz": bar(d, 100, 9),
            "si": a.get("serp_intent", ""),
            "sure": "%d/%d" % (a.get("sure", 0), a.get("sure", 0) + a.get("unsure", 0)),
            "flags": " ".join(a.get("flags", [])),
            "cpc": m.cpc, "vol": m.search_volume,
            "top": inc[0] if inc else ((a.get("top_domains") or [""])[0])}


# -- review -------------------------------------------------------------------
def cmd_review(args):
    market = Config.market()
    if args.state and args.keyword:
        with Run(seed="review", budget_usd=0.0) as run:
            ok = run.set_state(args.keyword.lower(), market, args.state, args.note)
        print("  %s -> %s%s" % (args.keyword, args.state,
                                "" if ok else "  (Keyword unbekannt)"))
        return 0 if ok else 1
    rows = keyword_rows(market, args.filter_state, args.limit)
    print(head("KEYWORDS  markt=%s%s" % (market, "  state=%s" % args.filter_state
                                         if args.filter_state else "")))
    print(table([{"kw": r["keyword"], "t": r["tier"], "d": r["difficulty"],
                  "cpc": r["cpc"], "vol": r["volume"], "st": r["state"],
                  "fl": r["flags"], "note": r["note"]} for r in rows],
                [("kw", "KEYWORD", 36, "l"), ("t", "T", 1, "r"), ("d", "DIFF", 4, "r"),
                 ("cpc", "CPC", 6, "r"), ("vol", "VOL", 6, "r"),
                 ("st", "STATE", 11, "l"), ("fl", "SIGNALE", 18, "l"),
                 ("note", "NOTIZ", 20, "l")]))
    print(rule("-"))
    print("  " + "  ".join("%s=%d" % kv for kv in sorted(state_counts(market).items())))
    return 0


# -- queue --------------------------------------------------------------------
def cmd_queue(args):
    from .classify import OVERRIDES_PATH, add_override, load_overrides
    if args.domain and args.type:
        print("  -> %s" % add_override(args.domain, args.type, args.note or ""))
        return 0
    ov = load_overrides()
    print(head("OVERRIDES  %d Eintraege" % len(ov)))
    for dom, (kind, note) in sorted(ov.items()):
        print("  %-30s %-12s %s" % (dom, kind, note))
    print(rule("-"))
    print("  Datei: %s" % OVERRIDES_PATH)
    print("  Direkt editierbar, oder: keyworld queue --domain X --type product")
    return 0


# -- costs / fetch ------------------------------------------------------------
def cmd_fetch(args):
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
            print("  %s  %5d Keywords  %s (bezahlt: $%.4f)"
                  % (t["task_id"][:18], len(metrics),
                     "OK" if done else "noch in Queue", t["cost_usd"]))
        print(rule("-"))
        print("  %d Keywords in den Cache geholt fuer $0.0000" % total)
    return 0


def cmd_costs(args):
    if args.run:
        from .store import connect
        shim = Run.__new__(Run)
        shim.conn, shim.run_id, shim.seed = connect(), args.run, ""
        shim.budget = Config.run_budget_usd
        print(cost_report(shim))
        return 0
    print(head("RUNS"))
    print(table([{"run_id": r["run_id"], "seed": r["seed"], "calls": r["calls"],
                  "cost": r["cost"], "status": r["status"]} for r in all_runs(args.limit)],
                [("run_id", "RUN", 13, "l"), ("seed", "SEED", 30, "l"),
                 ("calls", "CALLS", 5, "r"), ("cost", "USD", 8, "r"),
                 ("status", "STATUS", 7, "l")]))
    spent, saved, calls = lifetime_cost()
    print(rule("-"))
    print("  Gesamt: %s ueber %d Calls | durch Cache gespart: $%.4f" % (money(spent), calls, saved))
    return 0


# -- main ---------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(prog="keyworld", description="Nischen-Scouting")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("probe", help="Verbindung und Guthaben").set_defaults(fn=cmd_probe)

    g = sub.add_parser("gen", help="Themengebiete x Muster -> Keywords")
    g.add_argument("domain", nargs="*", default=[])
    g.add_argument("--domains", help="Datei mit Themengebieten")
    g.add_argument("--patterns", help="Datei mit Mustern (Default data/patterns.txt)")
    g.add_argument("--out")
    g.set_defaults(fn=cmd_gen)

    d = sub.add_parser("discover", help="Autocomplete-Kandidaten, kostenlos")
    d.add_argument("seed")
    d.add_argument("--limit", type=int, default=500)
    d.add_argument("--deep", action="store_true")
    d.add_argument("--out")
    d.set_defaults(fn=cmd_discover)

    s = sub.add_parser("scout", help="Kompletter Lauf -> CSV-Shortlist")
    s.add_argument("--domain", action="append", default=[], help="Themengebiet (mehrfach)")
    s.add_argument("--domains", help="Datei mit Themengebieten")
    s.add_argument("--patterns", help="Datei mit Mustern")
    s.add_argument("--keywords-file", help="fertige Keywords, eine je Zeile")
    s.add_argument("--seed", action="append", default=[], help="Autocomplete-Seed (mehrfach)")
    s.add_argument("--go", action="store_true", help="wirklich abrufen")
    s.add_argument("--limit", type=int, default=500)
    s.add_argument("--top", type=int, default=30)
    s.add_argument("--min-volume", type=int, default=300)
    s.add_argument("--max-volume", type=int, default=10000)
    s.add_argument("--min-cpc", type=float, default=0.50)
    s.add_argument("--budget", type=float, default=Config.run_budget_usd)
    s.add_argument("--live", action="store_true")
    s.add_argument("--max-serp", type=int, default=120)
    s.add_argument("--incumbent-share", type=float, default=0.25)
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--include-rejected", action="store_true")
    s.add_argument("--deep", action="store_true")
    s.add_argument("--csv")
    s.add_argument("--review-csv")
    s.add_argument("--harvest")
    s.add_argument("--json")
    s.add_argument("--report", help="HTML, niedrigste Prioritaet")
    s.set_defaults(fn=cmd_scout)

    r = sub.add_parser("review", help="Zustand und Notiz je Keyword")
    r.add_argument("keyword", nargs="?")
    r.add_argument("--state", choices=STATES)
    r.add_argument("--note")
    r.add_argument("--filter-state", choices=STATES)
    r.add_argument("--limit", type=int, default=60)
    r.set_defaults(fn=cmd_review)

    q = sub.add_parser("queue", help="Overrides anzeigen oder ergaenzen")
    q.add_argument("--domain")
    q.add_argument("--type", choices=("product", "article", "forum",
                                      "marketplace", "aggregator"))
    q.add_argument("--note")
    q.set_defaults(fn=cmd_queue)

    c = sub.add_parser("costs", help="Ausgabenuebersicht")
    c.add_argument("--run")
    c.add_argument("--limit", type=int, default=20)
    c.set_defaults(fn=cmd_costs)

    sub.add_parser("fetch", help="Offene Queue-Tasks einsammeln").set_defaults(fn=cmd_fetch)

    args = p.parse_args(argv)
    if getattr(args, "cmd", None) == "scout" and not (
            args.domain or args.domains or args.seed or args.keywords_file):
        p.error("scout braucht --domain, --domains, --seed oder --keywords-file")
    if not getattr(args, "fn", None):
        p.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
