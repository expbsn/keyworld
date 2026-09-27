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
from pathlib import Path

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
    from .classify import load_overrides
    from .export import write_csv, write_review_csv
    from .filters import Funnel, annotate_intent, order, split_tiers
    from .providers import DataForSEO
    from .serp import (analyse, attach_verdicts, find_incumbents_grouped,
                       review_queue)

    if not Config.has_dataforseo():
        print("  Keine DataForSEO-Credentials -- siehe .env.example", file=sys.stderr)
        return 1
    market = Config.market()
    label = ", ".join(args.domain or args.seed or [])
    if not label and args.domains:
        label = Path(args.domains).stem          # domains.txt -> "domains"
    if not label and args.keywords_file:
        label = Path(args.keywords_file).stem
    label = label or "lauf"

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

        # Schaetzung nur auf das, was NICHT im Cache liegt -- sonst zeigt der
        # Trockenlauf Kosten an, die gar nicht anfallen.
        fresh = [k for k in candidates if run.cache_get(dfs.name, k) is None]
        est_sv, tasks = dfs.estimate_search_volume_cost(len(fresh))
        if len(fresh) < len(candidates):
            print("  %d im Cache, %d neu abzurufen" % (len(candidates) - len(fresh), len(fresh)))
        print("  Layer 1: %d Task(s) -> %s" % (tasks, money(est_sv) if fresh else money(0.0)))
        if not args.go:
            print("  Layer 2: erst nach den Filtern bezifferbar (%s je Keyword)"
                  % money(dfs.estimate_serp_cost(1)))
            print(rule("-"))
            print("  %sTrockenlauf.%s Mit --go tatsaechlich abrufen." % (BOLD, RST))
            return 0

        metrics = dfs.search_volume(candidates, run)

        funnel = Funnel(args.min_volume, args.max_volume, args.min_cpc, args.min_trend)
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
        # Platzhirsche je Themengebiet, nicht je Lauf
        subjects = _subject_map(args, list(serps))
        inc_map = find_incumbents_grouped(serps, subjects, args.incumbent_share)
        incumbents = set().union(*inc_map.values()) if inc_map else set()
        analysis = dict((k, analyse(v, inc_map.get(k, set())))
                        for k, v in serps.items() if v)
        analysis = dict((k, v) for k, v in analysis.items() if v)
        n_thin = sum(1 for v in analysis.values() if v["difficulty"] is None)
        print("  Platzhirsche: %s" % (", ".join(sorted(incumbents)[:6]) or "keine"))
        if n_thin:
            print("  %s%d Keywords ohne belastbare Difficulty -- werden zuerst gezeigt%s"
                  % (DIM, n_thin, RST))

        if args.serp:
            keep = [m for m in survivors
                    if (analysis.get(m.keyword) or {}).get("serp_intent") == args.serp]
            print("  %d von %d mit SERP-Absicht %s" % (len(keep), len(survivors), args.serp))
            if not keep:
                print("  %skeine Treffer, zeige alle%s" % (DIM, RST))
            survivors = keep or survivors      # nie alles wegfiltern
        annotated = order(annotate_intent(survivors), lambda k: (analysis.get(k) or {}).get("difficulty"))
        visible, sunk = split_tiers(annotated)

        rows = []
        for m, lvl, name, _pat in annotated:
            a = analysis.get(m.keyword) or {}
            st = run.upsert_keyword(
                m.keyword, market, tier=lvl, difficulty=a.get("difficulty"),
                cpc=m.cpc, volume=m.search_volume,
                flags=" ".join(a.get("flags", [])),
                serp_intent=a.get("serp_intent", ""), trend=m.trend_ratio(),
                subject=subjects.get(m.keyword, ""),
                top_domains=" ".join(a.get("top_domains", [])[:4]),
                sure=a.get("sure"), unsure=a.get("unsure"), run_id=run.run_id,
                spread=a.get("spread"), season=m.seasonality())
            prev = known.get(m.keyword, {})
            rows.append(dict(m.to_dict(), tier=lvl, tier_name=name, serp=a or None,
                             state=prev.get("state", st), note=prev.get("note", ""),
                             market=market))
        run.commit()

        cols = [("kw", "KEYWORD", 32, "l"), ("tier", "T", 1, "r"),
                ("diff", "DIFF", 7, "r"), ("viz", "", 8, "l"),
                ("si", "SERP", 4, "l"), ("sure", "SICH", 4, "r"),
                ("trend", "TR", 4, "r"), ("season", "SAI", 4, "r"),
                ("flags", "SIGNALE", 16, "l"),
                ("cpc", "CPC", 6, "r"), ("vol", "VOL", 6, "r"),
                ("var", "VAR", 3, "r"), ("top", "TOP-DOMAIN", 18, "l")]

        from .cluster import group as cluster_group, summary as cluster_summary
        by_row = dict((r["keyword"], r) for r in rows)
        for title, grp in (("STUFE 1-3 UND OFFEN", visible), ("STUFE 4  Orientierung", sunk)):
            if not grp:
                continue
            print()
            print("%s%s%s" % (BOLD, title, RST))
            if args.flat:
                out = [_row(m, lvl, analysis, incumbents, 0) for m, lvl, _n, _p in grp[:args.top]]
            else:
                picked = cluster_group([by_row[m.keyword] for m, _l, _n, _p in grp],
                                       lambda k: subjects.get(k, k))
                info = cluster_summary(picked)
                print("  %s%d Gebiete aus %d Zeilen (%.1f Varianten je Gebiet)%s"
                      % (DIM, info["gebiete"], info["zeilen"], info["verdichtung"], RST))
                lookup = dict((m.keyword, (m, lvl)) for m, lvl, _n, _p in grp)
                out = []
                for _subj, g in list(picked.items())[:args.top]:
                    m, lvl = lookup[g["best"]["keyword"]]
                    out.append(_row(m, lvl, analysis, incumbents, len(g["variants"])))
            print(table(out, cols))

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


def _subject_map(args, keywords):
    """Ordnet jedes Keyword seinem Themengebiet zu -- laengste Uebereinstimmung.

    Seeds zaehlen als Gebiet. Ohne das faellt bei --seed-Laeufen jedes
    Keyword in eine eigene Gruppe, die Platzhirsch-Suche laeuft ueber je
    eine SERP und findet nie etwas -- der Term verschwaende still.
    """
    from .generate import read_list
    subjects = list(args.domain) + list(args.seed)
    if args.domains:
        subjects += read_list(args.domains)
    subjects = sorted(set(x.strip().lower() for x in subjects if x.strip()),
                      key=len, reverse=True)
    out = {}
    for kw in keywords:
        hit = next((x for x in subjects if x in kw), None)
        if hit is None:
            # Kein Gebiet erkannt: erstes Wort als Naeherung, damit verwandte
            # Keywords wenigstens zusammenfallen statt zu vereinzeln.
            hit = kw.split()[0] if kw.split() else kw
        out[kw] = hit
    return out


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


def _row(m, lvl, analysis, incumbents, n_variants=0):
    a = analysis.get(m.keyword) or {}
    d = a.get("difficulty")
    t = m.trend_ratio()
    inc = [x for x in a.get("top_domains", []) if x in incumbents]
    sp = a.get("spread")
    return {"kw": m.keyword, "tier": lvl,
            "diff": "?" if d is None else ("%d\u00b1%d" % (d, round(sp)) if sp else str(d)),
            "viz": bar(d, 100, 8),
            "trend": ("%.2f" % t)[1:] if t else "-",
            "season": ("%.1f" % m.seasonality()) if m.seasonality() else "-",
            "var": ("+%d" % n_variants) if n_variants else "",
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
    counts = state_counts(market)
    if counts:
        print("  " + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
    else:
        print("  %sNoch keine Keywords. Erst scouten:%s" % (DIM, RST))
        print("  ./keyworld.sh scout --domains data/domains.txt --go")
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


MONATE = ["", "Jan", "Feb", "Mrz", "Apr", "Mai", "Jun",
          "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def cmd_dive(args):
    from .dive import estimate, run_dive
    from .providers import DataForSEO
    if not Config.has_dataforseo():
        print("  Keine DataForSEO-Credentials.", file=sys.stderr)
        return 1

    with Run(seed="dive:%s" % args.keyword, budget_usd=args.budget) as run:
        dfs = DataForSEO(live=False)
        kosten, was = estimate(args.scope, args.max_serps, dfs)
        print(head("DIVE  %s  umfang=%s" % (args.keyword, args.scope)))
        print("  Geschaetzt: %s  (%s)" % (money(kosten), was))
        if not args.go and args.scope != "schnell":
            print(rule("-"))
            print("  %sTrockenlauf.%s Mit --go tatsaechlich abrufen." % (BOLD, RST))
            return 0

        d = run_dive(args.keyword, run, dfs, args.scope, args.max_serps)
        if d["gekauft"]:
            print("  Gekauft: %s" % ", ".join(d["gekauft"]))

        s = d["season"]
        if s:
            print()
            print("%sSAISON  %s%s" % (BOLD, s["keyword"], RST))
            print("  Spitze %d im %s, Tal %d, Verhaeltnis %.1fx, Jahresschnitt %s"
                  % (s["peak"], MONATE[s["peak_month"]], s["trough"], s["ratio"], s["avg"]))
            for y, mo, v in s["months"]:
                print("  %s %02d %6d  %s" % (MONATE[mo], y % 100, v,
                                             "#" * int(round(34 * v / s["peak"]))))

        g = d["geld"]
        if g:
            print()
            print("%sGELD IM MARKT%s" % (BOLD, RST))
            print("  CPC volumengewichtet: %.2f EUR   hoechster: %.2f EUR"
                  % (g["cpc_gewichtet"], g["cpc_max"]))
            print("  Wettbewerbsindex Median: %s von 100" % g["competition_median"])
            print("  Gesamtvolumen: %d/Monat" % g["volumen_gesamt"])
            print("  Einordnung: %s%s%s" % (BOLD, g["urteil"], RST))

        print()
        print("%sKEYWORD-UMFELD%s" % (BOLD, RST))
        rows = [{"kw": r["keyword"], "t": r["tier"], "vol": r["volume"],
                 "cpc": r["cpc"], "comp": r["competition"],
                 "sai": ("%.1f" % r["season"]) if r["season"] else "-",
                 "serp": "ja" if r["hat_serp"] else ""}
                for r in d["keywords"][:args.top]]
        print(table(rows, [("kw", "KEYWORD", 40, "l"), ("t", "T", 1, "r"),
                           ("vol", "VOL", 7, "r"), ("cpc", "CPC", 6, "r"),
                           ("comp", "COMP", 4, "r"), ("sai", "SAI", 4, "r"),
                           ("serp", "SERP", 4, "l")]))

        l = d["landschaft"]
        print()
        print("%sWER HAELT DIE PLAETZE%s  (%d SERPs, %d Treffer)"
              % (BOLD, RST, l["serps"], l["treffer"]))
        for k, v in sorted(l["anteile"].items(), key=lambda kv: -kv[1]):
            print("  %-12s %3.0f%%  %s" % (k, 100 * v, "#" * int(round(28 * v))))
        print()
        print(table([{"d": x["domain"], "n": x["hits"], "k": x["kind"]}
                     for x in l["top_domains"][:10]],
                    [("d", "DOMAIN", 34, "l"), ("n", "SERPS", 5, "r"),
                     ("k", "ART", 12, "l")]))

        f = d["fragen"]
        if f["related"]:
            print()
            print("%sVERWANDTE SUCHEN%s" % (BOLD, RST))
            for q in f["related"][:12]:
                print("  " + q)
        if f["paa"]:
            print()
            print("%sWAS NUTZER FRAGEN%s" % (BOLD, RST))
            for q in f["paa"][:10]:
                print("  " + q)

        print()
        print(cost_report(run))
        if args.json:
            open(args.json, "w", encoding="utf-8").write(
                json.dumps(d, ensure_ascii=False, indent=2))
            print("  -> %s" % args.json)
    return 0


def cmd_compare(args):
    """Gebiete nebeneinander, rein aus dem Cache. Kostet nichts."""
    from .dive import compare
    from .store import connect, keyword_rows

    market = Config.market()
    rows = keyword_rows(market, args.state)
    if args.serp:
        rows = [r for r in rows if (r["serp_intent"] or "") == args.serp]
    if not args.include_rejected:
        rows = [r for r in rows if r["state"] != "rejected"]
    subjects = [r["subject"] or r["keyword"].split()[0] for r in rows]

    print(head("VERGLEICH  %d Gebiete  markt=%s%s"
               % (len(set(subjects)), market,
                  "  serp=%s" % args.serp if args.serp else "")))
    if not subjects:
        print("  Nichts zu vergleichen. Erst scouten.")
        return 0

    erg = compare(subjects, connect())
    if not args.include_brands:
        marken = [r for r in erg if r["ist_marke"]]
        erg = [r for r in erg if not r["ist_marke"]]
        if marken:
            print("  %s%d Marken ausgeblendet (%s) -- dort hat jemand bereits"
                  " gewonnen, --include-brands zeigt sie%s"
                  % (DIM, len(marken), ", ".join(r["subject"] for r in marken[:5]), RST))
    print(table([{
        "g": r["subject"] + (" *" if r["ist_marke"] else ""),
        "kw": r["keywords"], "vol": r["volumen"],
        "cpc": ("%.2f" % r["cpc"]) if r["cpc"] else "-",
        "max": ("%.2f" % r["cpc_max"]) if r["cpc_max"] else "-",
        "comp": r["competition"], "sai": ("%.1f" % r["saison"]) if r["saison"] else "-",
        "prod": "%d%%" % round(100 * r["produkt_anteil"]),
        "gratis": "%d%%" % round(100 * r["gratis_anteil"]),
        "u": r["urteil"]} for r in erg[:args.top]],
        [("g", "GEBIET", 24, "l"), ("kw", "KW", 4, "r"), ("vol", "VOL/MON", 8, "r"),
         ("cpc", "CPC", 6, "r"), ("max", "MAX", 6, "r"), ("comp", "COMP", 4, "r"),
         ("sai", "SAI", 4, "r"), ("prod", "PROD", 5, "r"),
         ("gratis", "GRATIS", 6, "r"), ("u", "EINORDNUNG", 26, "l")]))
    print(rule("-"))
    lohnt = [r for r in erg if (r["cpc"] or 0) >= args.min_cpc]
    print("  %d von %d Gebieten ueber %.2f EUR gewichtetem CPC" %
          (len(lohnt), len(erg), args.min_cpc))
    if lohnt:
        print("  Bezahlter Dive lohnt bei: %s"
              % ", ".join(r["subject"] for r in lohnt[:6]))
    if args.json:
        open(args.json, "w", encoding="utf-8").write(
            json.dumps(erg, ensure_ascii=False, indent=2))
        print("  -> %s" % args.json)
    return 0


def cmd_rescore(args):
    """Urteile aus gespeicherten Rohsignalen neu ableiten. Kein Abruf, 0 $."""
    import json as _json
    from .classify import CONFIDENCE_FLOOR, load_overrides, override_verdict, rescore
    from .store import connect

    conn = connect()
    overrides = load_overrides()
    rows = conn.execute("SELECT cache_key, keyword, payload FROM kw_cache"
                        " WHERE source='page'").fetchall()
    changed = geprueft = 0
    vorher = nachher = 0
    for r in rows:
        try:
            old = _json.loads(r["payload"])
        except ValueError:
            continue
        geprueft += 1
        if old.get("kind") != "unknown" and old.get("confidence", 0) >= CONFIDENCE_FLOOR:
            vorher += 1
        new = override_verdict(r["keyword"], overrides) or rescore(old)
        if new.get("kind") != "unknown" and new.get("confidence", 0) >= CONFIDENCE_FLOOR:
            nachher += 1
        if new != old:
            changed += 1
            if not args.dry_run:
                conn.execute("UPDATE kw_cache SET payload=? WHERE cache_key=?",
                             (_json.dumps(new, ensure_ascii=False), r["cache_key"]))
    if not args.dry_run:
        conn.commit()

    print(head("RESCORE%s" % ("  (Trockenlauf)" if args.dry_run else "")))
    print("  %d Urteile geprueft, %d geaendert" % (geprueft, changed))
    print("  Sicher eingestuft: %d -> %d  (%.0f%% -> %.0f%%)"
          % (vorher, nachher, 100.0 * vorher / max(1, geprueft),
             100.0 * nachher / max(1, geprueft)))
    print(rule("-"))
    print("  Kein Abruf, keine Kosten. Danach den Scout erneut laufen lassen,")
    print("  damit Difficulty und Signale neu gerechnet werden.")
    return 0


def cmd_serve(args):
    from .ui import serve
    print(head("INTERFACE"))
    return serve(args.port, not args.no_open) or 0


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
        from .store import RunView
        view = RunView(args.run)
        if not view.exists:
            print("  Run %s nicht gefunden." % args.run, file=sys.stderr)
            return 1
        print(cost_report(view))
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
    s.add_argument("--min-trend", type=float,
                   help="0.8 = schrumpfende Nischen raus (Standard: nur anzeigen)")
    s.add_argument("--flat", action="store_true",
                   help="ohne Verdichtung je Gebiet, jede Zeile einzeln")
    s.add_argument("--budget", type=float, default=Config.run_budget_usd)
    s.add_argument("--live", action="store_true")
    s.add_argument("--serp", choices=("PROD", "MIX", "RED"),
                   help="nur diese SERP-Absicht; PROD heisst, dass Google "
                        "dort echte Produktseiten rankt")
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

    dv = sub.add_parser("dive", help="Tiefenanalyse eines Gebiets")
    dv.add_argument("keyword")
    dv.add_argument("--scope", choices=("schnell", "standard", "tief"),
                    default="schnell")
    dv.add_argument("--go", action="store_true", help="wirklich abrufen")
    dv.add_argument("--budget", type=float, default=Config.run_budget_usd)
    dv.add_argument("--max-serps", type=int, default=25)
    dv.add_argument("--top", type=int, default=20)
    dv.add_argument("--json")
    dv.set_defaults(fn=cmd_dive)

    cp = sub.add_parser("compare", help="Gebiete nebeneinander, aus dem Cache")
    cp.add_argument("--serp", choices=("PROD", "MIX", "RED"))
    cp.add_argument("--state", choices=STATES)
    cp.add_argument("--include-rejected", action="store_true")
    cp.add_argument("--include-brands", action="store_true",
                    help="Markennamen mitzeigen (Standard: aus)")
    cp.add_argument("--min-cpc", type=float, default=3.0)
    cp.add_argument("--top", type=int, default=30)
    cp.add_argument("--json")
    cp.set_defaults(fn=cmd_compare)

    rs = sub.add_parser("rescore", help="Urteile aus Rohsignalen neu ableiten")
    rs.add_argument("--dry-run", action="store_true")
    rs.set_defaults(fn=cmd_rescore)

    sv = sub.add_parser("serve", help="Lokales Interface im Browser")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-open", action="store_true")
    sv.set_defaults(fn=cmd_serve)

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
