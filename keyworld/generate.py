"""Kartesisches Produkt aus Themengebieten und Mustern.

Das Tippen von Keywords von Hand ist der Engpass, den dieses Tool beseitigt.
Ein Muster enthaelt {d} als Platzhalter fuer das Themengebiet; ohne
Platzhalter wird es als Suffix angehaengt.
"""
import re
from pathlib import Path


def read_list(path):
    """Eine Zeile je Eintrag. # leitet einen Kommentar ein."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def expand(domains, patterns):
    """-> Liste eindeutiger Keywords, Reihenfolge stabil."""
    out = []
    for d in domains:
        d = d.strip().lower()
        if not d:
            continue
        for p in patterns:
            p = p.strip().lower()
            if not p:
                continue
            kw = p.replace("{d}", d) if "{d}" in p else "%s %s" % (d, p)
            kw = re.sub(r"\s+", " ", kw).strip()
            if kw:
                out.append(kw)
    return list(dict.fromkeys(out))
