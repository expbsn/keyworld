"""Mono-Rendering fuer das Terminal. Keine Abhaengigkeiten, kein Framework."""
import os
import sys

W = 108
_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
DIM = "\033[2m" if _TTY else ""
BOLD = "\033[1m" if _TTY else ""
RST = "\033[0m" if _TTY else ""
GRN = "\033[32m" if _TTY else ""
YEL = "\033[33m" if _TTY else ""
RED = "\033[31m" if _TTY else ""


def rule(char="-", width=W):
    return char * width


def head(title, width=W):
    return "%s%s%s\n%s" % (BOLD, title, RST, rule("=", width))


def table(rows, cols, width=W):
    """rows: List[dict], cols: List[(key, label, width, align)]"""
    if not rows:
        return DIM + "  (keine Daten)" + RST
    head_line = "  ".join(
        (lbl[:w].ljust(w) if a == "l" else lbl[:w].rjust(w)) for _, lbl, w, a in cols)
    out = [BOLD + head_line + RST, rule("-", len(head_line))]
    for r in rows:
        cells = []
        for key, _, w, a in cols:
            v = r.get(key)
            s = "?" if v is None else (("%.4f" % v) if isinstance(v, float) else str(v))
            s = s[:w]
            cells.append(s.ljust(w) if a == "l" else s.rjust(w))
        out.append("  ".join(cells))
    return "\n".join(out)


def bar(value, vmax, width=20, char="#"):
    if not vmax or value is None:
        return ""
    n = max(0, min(width, int(round(width * (value / float(vmax))))))
    return char * n + DIM + "." * (width - n) + RST


def money(v):
    return "%s$%.4f%s" % (GRN if v == 0 else YEL, v, RST)


def cost_report(run, width=W):
    """Transparente Ausgabenuebersicht fuer einen Run."""
    lines = [head("AUSGABEN  run=%s  seed=%s" % (run.run_id, run.seed or "-"), width)]
    rows = run.breakdown()
    lines.append(table(rows, [
        ("layer", "LAYER", 5, "l"),
        ("provider", "PROVIDER", 16, "l"),
        ("endpoint", "ENDPOINT", 22, "l"),
        ("calls", "CALLS", 5, "r"),
        ("units", "UNITS", 7, "r"),
        ("cache_hits", "CACHE", 5, "r"),
        ("cost", "USD", 8, "r"),
        ("saved", "SPART", 7, "r"),
    ], width))
    spent, saved = run.total_cost(), run.total_saved()
    lines.append(rule("-", width))
    lines.append("  Ausgegeben : %s" % money(spent))
    lines.append("  Gespart    : %s$%.4f%s  (Cache)" % (GRN, saved, RST))
    lines.append("  Budget     : $%.2f   Rest: $%.4f" % (run.budget, run.budget - spent))
    return "\n".join(lines)
