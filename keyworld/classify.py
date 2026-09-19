"""Ergebnistyp-Klassifikation aus Seiteninhalten.

Grundregel: eine unbekannte Domain wird NIE als Produktseite geraten. Sie
heisst `unknown` und landet in der Pruefliste. Stillschweigend "Produkt" zu
raten treibt die Difficulty hoch und versteckt genau die Chancen, die dieses
Tool finden soll -- ein Fehler, den man der Ausgabe nicht ansieht.

Jede Einstufung traegt eine Confidence und die Signale, die zu ihr gefuehrt
haben. Unter der Schwelle wird nichts bewertet, sondern zur Pruefung gelegt.

Korrekturen sind Daten, kein Code: `data/overrides.csv` (domain,type,note)
wird bei jedem Lauf geladen und schlaegt jede Heuristik.
"""
import concurrent.futures
import csv
import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import ROOT

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
OVERRIDES_PATH = ROOT / "data" / "overrides.csv"
MAX_BYTES = 400_000
TIMEOUT = 12
STALE_YEARS = 3
CONFIDENCE_FLOOR = 0.50

KINDS = ("product", "article", "forum", "marketplace", "aggregator",
         "download", "institution", "unknown")


# -- Domains, bei denen die Zuordnung sicher ist ------------------------------
FORUM_DOMAINS = {          # UGC und soziale Plattformen: nie eine Produktseite
    "reddit.com", "quora.com", "gutefrage.net", "wer-weiss-was.de",
    "stackoverflow.com", "stackexchange.com", "superuser.com", "serverfault.com",
    "discourse.org", "xing.com", "linkedin.com", "facebook.com",
    "youtube.com", "youtu.be", "vimeo.com", "pinterest.com", "pinterest.de",
    "instagram.com", "tiktok.com", "x.com", "twitter.com", "slideshare.net",
    "scribd.com", "issuu.com",
}
MARKETPLACE_DOMAINS = {
    "amazon.de", "amazon.com", "ebay.de", "ebay.com", "etsy.com", "otto.de",
    "kaufland.de", "idealo.de", "galaxus.de", "alibaba.com", "aliexpress.com",
    "temu.com", "apps.apple.com", "play.google.com", "microsoft.com",
    "cloud.microsoft", "office.com", "canva.com", "notion.so", "figma.com",
    "templates.office.com", "chrome.google.com", "addons.mozilla.org",
}
AGGREGATOR_DOMAINS = {
    "capterra.com", "capterra.com.de", "g2.com", "getapp.de", "getapp.com",
    "softwareadvice.com", "omr.com", "trusted.de", "sortlist.de", "wlw.de",
    "testberichte.de", "vergleich.org", "netzsieger.de", "check24.de",
    "verivox.de", "stiftung-warentest.de", "test.de", "oekotest.de",
}
ARTICLE_DOMAINS = {
    "welt.de", "spiegel.de", "stern.de", "focus.de", "chip.de", "heise.de",
    "computerbild.de", "t3n.de", "giga.de", "netzwelt.de", "t-online.de",
    "n-tv.de", "faz.net", "swr3.de", "merkur.de", "wikipedia.org", "medium.com",
}

# -- Inhaltssignale -----------------------------------------------------------
LD_PRODUCT = {"softwareapplication", "product", "offer", "service", "webapplication"}
LD_ARTICLE = {"article", "blogposting", "newsarticle", "techarticle", "report"}
LD_FORUM = {"discussionforumposting"}   # QAPage/FAQPage steht heute
                                       # auf jeder Marketingseite

# Vorlagen-Downloadseiten. Fuer dieses Tool die interessanteste Kategorie:
# der Markt will eine Vorlage, und wer rankt, verschenkt sie -- kein Produkt.
DOWNLOAD_PATHS = [r"/download", r"vorlage", r"muster", r"template", r"formular",
                  r"/vordruck", r"/arbeitshilfe"]
DOWNLOAD_LINKS = re.compile(
    r'href=["\'][^"\']+\.(xlsx?|xlsm|csv|ods|docx?|pptx?|pdf|zip)["\'?]', re.I)

# Kammern, Verbaende, Behoerden, Berufsgenossenschaften. Ranken auf Autoritaet,
# verkaufen aber nichts -- der Platz ist nicht von einem Produkt besetzt.
INSTITUTION_DOMAINS = {
    "wko.at", "ihk.de", "dihk.de", "handwerkskammer.de", "hwk.de",
    "bghm.de", "bgbau.de", "bgw-online.de", "dguv.de", "baua.de",
    "rki.de", "bfarm.de", "bzga.de", "gesundheit.gv.at", "kbv.de",
    "arbeitsagentur.de", "bmas.de", "bundesfinanzministerium.de",
    "datenschutz.de", "bfdi.bund.de", "verbraucherzentrale.de",
    "mieterbund.de", "mieterschutzbund.de", "haus-und-grund.net",
    "aok.de", "tk.de", "barmer.de", "dak.de", "ikk-classic.de", "kkh.de",
    "vbg.de", "bgn.de", "bgetem.de", "bgrci.de", "vdek.de", "gkv-spitzenverband.de",
}
INSTITUTION_PATTERNS = [r"\.bund\.de$", r"\.gv\.at$", r"\.admin\.ch$",
                        r"^kv[a-z]+\.de$", r"^bg[a-z]{0,5}\.de$", r"kammer",
                        r"verein\.", r"-verein\.", r"verband", r"innung",
                        r"genossenschaft", r"(^|\.)uni-", r"\.uni-",
                        r"hochschule", r"^fh-", r"\.edu$", r"^stadt-", r"landkreis"]
# Auch der Pfad verraet Verbandsseiten: hausundgrund.de/verein/dachau/...
INSTITUTION_PATHS = [r"/verein", r"/verband", r"/kammer", r"/mitglied",
                     r"/satzung", r"/geschaeftsstelle"]

# Anbietersignale, die auch ohne Structured Data greifen: die Navigation
# einer Softwareseite fuehrt praktisch immer zu Preisen oder Funktionen.
RE_NAV_PRODUCT = re.compile(
    r'href=["\'][^"\']*/(preise|pricing|plans|tarife|funktionen|features|'
    r'produkt|product|demo|testen|trial|abo)[/"\']', re.I)
RE_VENDOR_TXT = re.compile(
    r"(jetzt anfragen|demo vereinbaren|demo buchen|unverbindlich testen|"
    r"kostenlose testphase|zur (software|app)|unsere (software|l(oe|ö)sung)|"
    r"in den warenkorb|zzgl\. mwst|inkl\. mwst)", re.I)

PATH_PRODUCT = [r"/pricing", r"/preise", r"/plans", r"/tarife", r"/signup",
                r"/registrier", r"/kaufen", r"/produkt", r"/features", r"/testen",
                # Anbieterseiten ohne Structured Data verraten sich im Pfad
                r"/software", r"/cloud", r"[-/]app(/|$|\.)", r"/tool",
                r"/anwendung", r"/loesung", r"/l(oe|ö)sung", r"/leistungen"]
PATH_ARTICLE = [r"/blog", r"/magazin", r"/ratgeber", r"/news", r"/artikel",
                r"/wissen", r"/guide", r"/tipps", r"/journal", r"/lexikon", r"/academy"]
PATH_FORUM = [r"/thread", r"/topic", r"/forum", r"/viewtopic", r"/t/", r"/comments/",
              r"/questions/", r"/showthread"]

RE_PRICE = re.compile(
    r"(\d+[,.]\d{2}\s*(?:€|eur)|(?:€|eur|\$)\s*\d+)[^<]{0,24}"
    r"(?:/|pro |per )\s*(?:monat|month|mo\b|jahr|year|nutzer|user|platz)", re.I)
RE_CTA = re.compile(
    r"(kostenlos testen|jetzt testen|kostenlos starten|jetzt starten|"
    r"free trial|start free|demo buchen|demo anfordern|jetzt kaufen|"
    r"in den warenkorb|kostenlos registrieren|14 tage kostenlos)", re.I)
RE_AUTHOR = re.compile(
    r'(rel=["\']author|itemprop=["\']author|name=["\']author["\']|'
    r'class=["\'][^"\']*\bauthor\b|>\s*von\s+[A-ZÄÖÜ][a-zäöüß]+\s+[A-ZÄÖÜ])')
RE_FORUM_TXT = re.compile(
    r"(\d+\s*(antworten|beitr(ae|ä)ge|replies|posts|kommentare)|"
    r"thema erstellt|zitieren|threadstarter|\bforenregeln\b)", re.I)
RE_URL_DATE = re.compile(r"/(20\d\d)/(\d{1,2})(?:/|-)")
RE_DATE_META = re.compile(
    r'(?:article:published_time|datePublished)["\']?\s*[:=]\s*["\']'
    r'(\d{4}-\d{2}-\d{2})', re.I)
RE_LANG = re.compile(r'<html[^>]*\blang=["\']?([a-zA-Z-]{2,7})')
RE_LD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I)
DOWNLOAD_EXT = (".xlsx", ".xls", ".csv", ".ods", ".docx", ".doc", ".numbers")


def _norm(domain):
    d = (domain or "").lower().lstrip(".")
    return d[4:] if d.startswith("www.") else d


def _suffixes(domain):
    """sub.excel.cloud.microsoft -> die Kette bis hinunter zu cloud.microsoft."""
    parts = _norm(domain).split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def _in_group(domain, group):
    return next((s for s in _suffixes(domain) if s in group), None)


def _base(domain):
    parts = _norm(domain).split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else _norm(domain)


# -- Overrides: Daten, nicht Code ---------------------------------------------
def load_overrides(path=None):
    """-> {domain: (type, note)}. Fehlt die Datei, wird sie angelegt."""
    p = Path(path or OVERRIDES_PATH)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "# Korrekturen fuer die Klassifikation. Schlaegt jede Heuristik.\n"
            "# type: product | article | forum | marketplace | aggregator\n"
            "domain,type,note\n", encoding="utf-8")
        return {}
    out = {}
    with p.open(encoding="utf-8") as fh:
        for row in csv.DictReader(l for l in fh if not l.startswith("#")):
            dom = (row.get("domain") or "").strip().lower()
            kind = (row.get("type") or "").strip().lower()
            if dom and kind in KINDS:
                out[_norm(dom)] = (kind, (row.get("note") or "").strip())
    return out


def add_override(domain, kind, note="", path=None):
    p = Path(path or OVERRIDES_PATH)
    load_overrides(p)
    with p.open("a", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerow([_norm(domain), kind, note])
    return str(p)


# -- Seitenabruf --------------------------------------------------------------
_domain_lock = threading.Lock()
_last_hit = {}


def _polite(domain, min_gap=0.7):
    with _domain_lock:
        last = _last_hit.get(domain, 0.0)
        wait = min_gap - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        _last_hit[domain] = time.time()


def looks_like_file(url):
    """Dateiendung in der URL -- Abruf unnoetig, Einordnung eindeutig."""
    path = (urlparse(url or "").path or "").lower()
    return path.endswith(DOWNLOAD_EXT + (".pdf", ".zip", ".pptx", ".ppt"))


def fetch(url, session=None):
    """-> (html, final_url, error). Begrenzt auf MAX_BYTES."""
    s = session or requests.Session()
    try:
        _polite(_norm(urlparse(url).netloc))
        r = s.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True,
                  headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.7"})
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "xml" not in ctype:
            return "", r.url, "kein HTML (%s)" % ctype.split(";")[0]
        chunks, total = [], 0
        for chunk in r.iter_content(16384, decode_unicode=False):
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_BYTES:
                break
        r.close()
        enc = r.encoding or "utf-8"
        return b"".join(chunks).decode(enc, errors="replace"), r.url, None
    except requests.RequestException as e:
        return "", url, type(e).__name__


# -- Signalauswertung ---------------------------------------------------------
def _ld_types(html_text):
    types = set()
    for block in RE_LD.findall(html_text or "")[:8]:
        try:
            data = json.loads(block.strip())
        except ValueError:
            for m in re.findall(r'"@type"\s*:\s*"([^"]+)"', block):
                types.add(m.lower())
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                t = node.get("@type")
                for v in ([t] if isinstance(t, str) else (t or [])):
                    if isinstance(v, str):
                        types.add(v.lower())
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
    return types


def signals_for(url, html_text):
    """-> (scores {kind: punkte}, gruende [str], meta {lang, published, download})"""
    scores = dict((k, 0) for k in KINDS if k != "unknown")
    why, path = [], (urlparse(url).path or "").lower()
    meta = {"lang": None, "published": None, "download": False}

    if looks_like_file(url):
        meta["download"] = True
        scores["download"] += 5
        why.append("Download-Datei")

    for pats, kind, pts, tag in ((PATH_PRODUCT, "product", 3, "Pfad"),
                                 (PATH_ARTICLE, "article", 2, "Pfad"),
                                 (PATH_FORUM, "forum", 2, "Pfad")):
        hit = next((p for p in pats if re.search(p, path)), None)
        if hit:
            scores[kind] += pts
            why.append("%s %s" % (tag, hit.strip("/")))

    m = RE_URL_DATE.search(path)
    if m:
        scores["article"] += 2
        meta["published"] = "%s-%02d-01" % (m.group(1), int(m.group(2)))
        why.append("Datum in URL %s" % m.group(1))

    dom = _norm(urlparse(url or "").netloc)
    if _in_group(dom, INSTITUTION_DOMAINS) or any(
            re.search(p, dom) for p in INSTITUTION_PATTERNS):
        scores["institution"] += 4
        why.append("Institution/Verband")
    elif any(re.search(p, path) for p in INSTITUTION_PATHS):
        scores["institution"] += 2
        why.append("Verbandsbereich im Pfad")

    if not html_text:
        return scores, why, meta

    types = _ld_types(html_text)
    for group, kind, tag in ((LD_PRODUCT, "product", "Produkt"),
                             (LD_ARTICLE, "article", "Artikel"),
                             (LD_FORUM, "forum", "Forum")):
        hit = types & group
        if hit:
            scores[kind] += 3
            why.append("Structured Data %s" % "/".join(sorted(hit)))

    hit = next((p for p in DOWNLOAD_PATHS if re.search(p, path)), None)
    if hit and DOWNLOAD_LINKS.search(html_text):
        scores["download"] += 3
        meta["download"] = True
        why.append("Vorlagenseite mit Dateilink")

    if RE_NAV_PRODUCT.search(html_text):
        scores["product"] += 2
        why.append("Navigation zu Preisen/Funktionen")
    if RE_VENDOR_TXT.search(html_text):
        scores["product"] += 2
        why.append("Anbietersprache")

    if RE_PRICE.search(html_text):
        scores["product"] += 2
        why.append("Preisangabe pro Zeitraum")
    if RE_CTA.search(html_text):
        scores["product"] += 2
        why.append("Produkt-CTA")
    if RE_AUTHOR.search(html_text):
        scores["article"] += 2
        why.append("Autoren-Markup")
    if RE_FORUM_TXT.search(html_text):
        scores["forum"] += 2
        why.append("Thread-Markup")

    d = RE_DATE_META.search(html_text)
    if d:
        meta["published"] = d.group(1)
        # Ein Datum allein ist ein CMS-Artefakt und beweist keinen Artikel.
        # Es zaehlt nur, wenn ein zweites Artikelsignal da ist -- sonst
        # druecken Produktseiten mit Datum unter die Schwelle.
        if scores["article"] > 0:
            scores["article"] += 1
        why.append("Veroeffentlichungsdatum %s" % d.group(1))
    lang = RE_LANG.search(html_text)
    if lang:
        meta["lang"] = lang.group(1).split("-")[0].lower()
    return scores, why, meta


def verdict_from_scores(scores, why, meta):
    total = sum(scores.values())
    if total == 0:
        return {"kind": "unknown", "confidence": 0.0, "why": why or ["keine Signale"],
                "scores": {}, "lang": meta["lang"],
                "published": meta["published"], "download": meta["download"]}
    kind = max(scores, key=lambda k: scores[k])
    best = scores[kind]
    # Reinheit (wie widerspruchsfrei) mal Beleglage (wie viel ueberhaupt).
    # Ein einzelnes schwaches Signal landet unter der Schwelle, zwei
    # uebereinstimmende darueber, ein Widerspruch faellt durch.
    purity = best / total
    evidence = min(1.0, 0.35 + 0.65 * (best / 5.0))
    confidence = round(purity * evidence, 2)
    return {"kind": kind, "confidence": confidence, "why": why,
            "scores": dict((k, v) for k, v in scores.items() if v),
            "lang": meta["lang"], "published": meta["published"],
            "download": meta["download"]}


def override_verdict(domain, overrides):
    """Deine Korrektur, falls es eine gibt. Schlaegt ALLES -- auch den Cache.

    Ein gecachtes Urteil ist selbst ein Heuristik-Ergebnis. Duerfte es das
    Override ueberstimmen, waere eine Zeile in overrides.csv bis zum Ablauf
    des Caches wirkungslos.
    """
    hit = _in_group(_norm(domain), overrides)
    if not hit:
        return None
    kind, note = overrides[hit]
    return {"kind": kind, "confidence": 1.0, "why": ["override: %s" % (note or "-")],
            "lang": None, "published": None, "download": False, "source": "override"}


def classify_url(url, domain, overrides, session=None, fetch_page=True):
    dom = _norm(domain)

    forced = override_verdict(dom, overrides)
    if forced:
        return forced

    for group, kind in ((FORUM_DOMAINS, "forum"), (MARKETPLACE_DOMAINS, "marketplace"),
                        (AGGREGATOR_DOMAINS, "aggregator"), (ARTICLE_DOMAINS, "article"),
                        (INSTITUTION_DOMAINS, "institution")):
        if _in_group(dom, group):
            return {"kind": kind, "confidence": 0.95, "why": ["bekannte Domain"],
                    "lang": None, "published": None, "download": False, "source": "liste"}

    if looks_like_file(url):
        scores, why, meta = signals_for(url, "")
        v = verdict_from_scores(scores, why, meta)
        v["source"] = "datei"
        return v

    html_text, final, err = ("", url, None) if not fetch_page else fetch(url, session)
    scores, why, meta = signals_for(final, html_text)
    if err:
        why.append("Abruf: %s" % err)
    v = verdict_from_scores(scores, why, meta)
    v["source"] = "seite"
    return v


def classify_many(results, overrides=None, workers=8, fetch_page=True, cache=None):
    """results: [{domain, url}] -> {url: verdict}. Nebenlaeufig, mit Cache-Hook."""
    overrides = load_overrides() if overrides is None else overrides
    out, todo = {}, []
    for r in results:
        # Reihenfolge ist entscheidend: Override vor Cache, sonst wirkt eine
        # frische Korrektur erst nach Ablauf der Cache-Frist.
        forced = override_verdict(r["domain"], overrides)
        if forced:
            out[r["url"]] = forced
            continue
        hit = cache.get(r["url"]) if cache else None
        if hit is not None:
            out[r["url"]] = hit
        else:
            todo.append(r)
    if not todo:
        return out

    session = requests.Session()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = dict((pool.submit(classify_url, r["url"], r["domain"], overrides,
                                    session, fetch_page), r) for r in todo)
        for fut in concurrent.futures.as_completed(futures):
            r = futures[fut]
            try:
                out[r["url"]] = fut.result()
            except Exception as e:                      # noqa: BLE001
                out[r["url"]] = {"kind": "unknown", "confidence": 0.0,
                                 "why": ["Fehler: %s" % type(e).__name__],
                                 "lang": None, "published": None,
                                 "download": False, "source": "fehler"}
    return out


def is_stale(published, years=STALE_YEARS):
    if not published:
        return False
    try:
        dt = datetime.strptime(published[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return dt < datetime.now() - timedelta(days=365 * years)
