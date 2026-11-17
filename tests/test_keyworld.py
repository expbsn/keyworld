"""Tests fuer die reinen Funktionen. Kein Netz, keine API, keine Kosten.

    ./.venv/bin/python -m unittest discover -s tests -v
"""
import contextlib
import io
import re
import unittest
from pathlib import Path

from keyworld import dive, render
from keyworld.cli import main
from keyworld.classify import (_in_group, _suffixes, is_stale, looks_like_file,
                               rescore, signals_for, verdict_from_scores)
from keyworld.cluster import best_of, group
from keyworld.filters import Funnel
from keyworld.generate import expand
from keyworld.intent import RANK, classify, is_visible
from keyworld.providers.base import KeywordMetrics
from keyworld.serp import analyse, attach_verdicts, find_incumbents, weak_signals


def km(keyword, vol=1000, cpc=2.0, monthly=None):
    return KeywordMetrics(keyword=keyword, search_volume=vol, cpc=cpc,
                          monthly=monthly or [])


class Intent(unittest.TestCase):
    def test_stufen(self):
        for kw, want in [("rechnung in excel erstellen", 1),
                         ("dienstplan vorlage", 1),
                         ("hygieneplan muster", 1),
                         ("trello alternative", 2),
                         ("lexoffice preise", 2),
                         ("dienstplan app", 2),
                         ("zeiterfassung pflicht 2026", 3),
                         ("nebenkostenabrechnung frist", 3),
                         ("beste buchhaltungs app", 4),
                         ("wie funktioniert faktura", 4)]:
            self.assertEqual(classify(kw)[0], want, kw)

    def test_keine_fehlzuendung_bei_wortgrenzen(self):
        """nebenkostenabrechnung enthaelt 'kosten', kostenlos enthaelt 'kosten'."""
        self.assertEqual(classify("nebenkostenabrechnung vorlage")[0], 1)
        self.assertEqual(classify("reisekostenabrechnung excel vorlage")[0], 1)
        self.assertNotEqual(classify("stundenplan kostenlos")[0], 2)
        self.assertEqual(classify("preisliste vorlage")[0], 1)

    def test_offen_sinkt_nicht_auf_stufe_vier(self):
        self.assertEqual(classify("xyzabc quux")[0], 0)
        self.assertTrue(is_visible(0))
        self.assertFalse(is_visible(4))
        self.assertLess(RANK[0], RANK[4])


class Generator(unittest.TestCase):
    def test_platzhalter_und_suffix(self):
        out = expand(["dienstplan"], ["{d} vorlage", "alternative zu {d}", "app"])
        self.assertEqual(out, ["dienstplan vorlage", "alternative zu dienstplan",
                               "dienstplan app"])

    def test_eindeutig_und_stabil(self):
        out = expand(["a", "a"], ["{d} x", "{d} x"])
        self.assertEqual(out, ["a x"])


class Filter(unittest.TestCase):
    def test_reihenfolge_und_abgaenge(self):
        f = Funnel()
        out = f.run([km("zu klein", vol=100), km("zu gross", vol=50000),
                     km("kein geld", cpc=0.10), km("gut")])
        self.assertEqual([m.keyword for m in out], ["gut"])
        st = f.stages
        self.assertEqual(st["Volumen < 300"]["raus"], 1)
        self.assertEqual(st["Volumen > 10000"]["raus"], 1)
        self.assertEqual(st["CPC < 0.50"]["raus"], 1)

    def test_unbekannter_trend_ueberlebt(self):
        """Fehlerbias: was man nicht weiss, wird nicht aussortiert."""
        f = Funnel(min_trend=0.8)
        out = f.run([km("ohne historie")])
        self.assertEqual(len(out), 1)


class Signale(unittest.TestCase):
    def test_datum_allein_beweist_keinen_artikel(self):
        """Produktseite mit CMS-Datum darf nicht unter die Schwelle fallen."""
        html = ('<html><a href="/preise/">Preise</a>'
                '<p>kostenlos testen</p>"datePublished":"2024-02-01"</html>')
        sc, why, meta = signals_for("https://x.de/", html)
        v = verdict_from_scores(sc, why, meta)
        self.assertEqual(v["kind"], "product")
        self.assertGreaterEqual(v["confidence"], 0.5)

    def test_datum_zaehlt_mit_zweitem_artikelsignal(self):
        html = ('<html><meta name="author" content="A">'
                '"@type":"Article" "datePublished":"2019-01-01"</html>')
        sc, why, meta = signals_for("https://x.de/blog/y", html)
        v = verdict_from_scores(sc, why, meta)
        self.assertEqual(v["kind"], "article")

    def test_unbekannt_wird_nie_produkt(self):
        sc, why, meta = signals_for("https://unbekannt.de/seite", "<html>nix</html>")
        v = verdict_from_scores(sc, why, meta)
        self.assertEqual(v["kind"], "unknown")
        self.assertEqual(v["confidence"], 0.0)

    def test_confidence_kalibrierung(self):
        def conf(scores):
            full = dict((k, 0) for k in ("product", "article", "forum",
                                         "marketplace", "aggregator",
                                         "download", "institution"))
            full.update(scores)
            return verdict_from_scores(full, [], {"lang": None, "published": None,
                                                  "download": False})["confidence"]
        self.assertLess(conf({"article": 1}), 0.5)          # ein schwaches Signal
        self.assertGreaterEqual(conf({"product": 2}), 0.5)  # ein klares
        self.assertLess(conf({"product": 3, "article": 3}), 0.5)  # Widerspruch

    def test_datei_ohne_abruf(self):
        self.assertTrue(looks_like_file("https://x.de/Muster.pdf"))
        self.assertTrue(looks_like_file("https://x.de/vorlage.xlsx"))
        self.assertFalse(looks_like_file("https://x.de/vorlage"))

    def test_domain_kette(self):
        self.assertEqual(_suffixes("a.b.c.de")[-1], "de")
        self.assertEqual(_in_group("excel.cloud.microsoft", {"cloud.microsoft"}),
                         "cloud.microsoft")

    def test_stale(self):
        self.assertTrue(is_stale("2019-01-01"))
        self.assertFalse(is_stale(None))
        self.assertFalse(is_stale("kaputt"))


def serp_of(kinds, **kw):
    org = []
    for i, k in enumerate(kinds, 1):
        org.append({"rank": i, "domain": "d%d.de" % i, "url": "https://d%d.de/x" % i,
                    "title": "t", "kind": k, "confidence": 0.9, "lang": "de",
                    "published": None, "download": False, "why": [],
                    "sure": k != "unknown"})
    for k, v in kw.items():
        for o in org:
            o[k] = v
    return {"organics": org, "related": [], "paa": [], "ads": 0}


class Serp(unittest.TestCase):
    def test_nur_produktseiten_besetzen(self):
        a = analyse(serp_of(["product"] * 10))
        b = analyse(serp_of(["article", "forum", "download", "institution",
                             "aggregator", "article", "forum", "download",
                             "institution", "article"]))
        self.assertGreater(a["difficulty"], b["difficulty"])
        self.assertEqual(b["product_share"], 0.0)

    def test_zu_duenne_beleglage_wird_nicht_geraten(self):
        a = analyse(serp_of(["unknown", "unknown", "product", "unknown",
                             "unknown", "unknown", "unknown", "unknown"]))
        self.assertIsNone(a["difficulty"])
        self.assertTrue(a["review"])

    def test_unsichere_treiben_difficulty_nicht_hoch(self):
        """Fehlerbias: unbekannt darf nicht wie 'besetzt' wirken."""
        klar = analyse(serp_of(["product", "article", "article", "article"]))
        mit_unbekannt = analyse(serp_of(["product", "article", "article",
                                         "article", "unknown", "unknown"]))
        self.assertEqual(klar["difficulty"], mit_unbekannt["difficulty"])

    def test_schwaechesignale(self):
        f, _ = weak_signals(serp_of(["forum", "article", "article", "product", "product"]))
        self.assertIn("FORUM", f)
        self.assertIn("ARTIKEL2", f)
        f, _ = weak_signals(serp_of(["download", "product", "product"]))
        self.assertIn("VORLAGE", f)
        f, _ = weak_signals(serp_of(["institution", "product", "product"]))
        self.assertIn("AMT", f)
        f, _ = weak_signals(serp_of(["product"] * 5, lang="en"))
        self.assertIn("NUR-EN", f)
        f, _ = weak_signals(serp_of(["product"] * 5, published="2018-01-01"))
        self.assertIn("ALT", f)

    def test_platzhirsche(self):
        s1 = serp_of(["product"] * 3)
        s2 = serp_of(["product"] * 3)
        inc = find_incumbents({"a": s1, "b": s2}, 0.25)
        self.assertIn("d1.de", inc)


class Verdichtung(unittest.TestCase):
    def test_beste_zeile_und_varianten(self):
        rows = [{"keyword": "x vorlage", "cpc": 2.0, "serp": {"difficulty": 40},
                 "search_volume": 100},
                {"keyword": "x excel vorlage", "cpc": 3.0, "serp": {"difficulty": 12},
                 "search_volume": 500},
                {"keyword": "y app", "cpc": 5.0, "serp": {"difficulty": 30},
                 "search_volume": 900}]
        g = group(rows, lambda k: k.split()[0])
        self.assertEqual(list(g), ["x", "y"])          # nach Difficulty sortiert
        self.assertEqual(g["x"]["best"]["keyword"], "x excel vorlage")
        self.assertEqual(len(g["x"]["variants"]), 1)

    def test_unbekannte_difficulty_gewinnt(self):
        rows = [{"keyword": "a", "cpc": 1.0, "serp": {"difficulty": 5}},
                {"keyword": "b", "cpc": 1.0, "serp": {"difficulty": None}}]
        self.assertEqual(best_of(rows)["keyword"], "b")


class Dive(unittest.TestCase):
    def test_stamm(self):
        self.assertEqual(dive.stem("stundenplan app"), "stundenplan")
        self.assertEqual(dive.stem("  Rechnung schreiben "), "rechnung")

    def test_monetarisierung_gewichtet_nach_volumen(self):
        """Ein teures Keyword mit 10 Suchen darf den Markt nicht schoenrechnen."""
        m = [km("viel billig", vol=10000, cpc=0.30),
             km("wenig teuer", vol=10, cpc=20.0)]
        g = dive.monetarisierung(m)
        # 10000*0,30 + 10*20 = 3200 / 10010 = 0,32 -- das teure Keyword
        # verschiebt das Urteil nicht, obwohl sein CPC 60x hoeher liegt
        self.assertEqual(g["cpc_gewichtet"], 0.32)
        self.assertEqual(g["cpc_max"], 20.0)
        self.assertEqual(g["urteil"], "Informationssuche, kein Markt")

    def test_urteil_skaliert(self):
        self.assertEqual(dive.monetarisierung([km("a", cpc=0.20)])["urteil"],
                         "Informationssuche, kein Markt")
        self.assertEqual(dive.monetarisierung([km("a", cpc=12.0)])["urteil"],
                         "harter B2B-Markt")

    def test_saisonkurve(self):
        vols = [1300, 720, 320, 210, 320, 320, 390, 320, 210, 210, 210, 590]
        m = km("x", monthly=[{"year": 2026, "month": i + 1, "search_volume": v}
                             for i, v in enumerate(vols)])
        s = dive.season_curve([m])
        self.assertEqual(s["peak"], 1300)
        self.assertEqual(s["trough"], 210)
        self.assertEqual(s["ratio"], 6.2)

    def test_schnell_kostet_nichts(self):
        class T:
            def estimate_search_volume_cost(self, n): return 0.06, 1
            def estimate_serp_cost(self, n): return n * 0.0006
        self.assertEqual(dive.estimate("schnell", 25, T())[0], 0.0)
        self.assertGreater(dive.estimate("tief", 25, T())[0],
                           dive.estimate("standard", 25, T())[0])


class Luecken(unittest.TestCase):
    """Die Umkehrung der PROD-Sicht: wo niemand verkauft."""

    def test_ein_anbieter_reicht_zum_ausschluss(self):
        ok, grund = dive.ist_luecke(["article", "forum", "article", "product"])
        self.assertFalse(ok)
        self.assertIn("Anbieter", grund)

    def test_luecke_wird_erkannt(self):
        ok, _ = dive.ist_luecke(["article", "forum", "article", "institution"])
        self.assertTrue(ok)

    def test_duenne_beleglage_ist_keine_luecke(self):
        """Ohne genug sichere Treffer ist 'kein Anbieter' nicht belegbar."""
        ok, grund = dive.ist_luecke(["article", None, None, None])
        self.assertFalse(ok)
        self.assertIn("sichere", grund)

    def test_gratisware_ist_keine_luecke(self):
        """Pinterest, Etsy und Canva heissen: Vorlagen, nicht unbedienter Bedarf."""
        ok, grund = dive.ist_luecke(["marketplace", "marketplace", "download",
                                     "article"])
        self.assertFalse(ok)
        self.assertIn("Gratisware", grund)

    def test_ohne_antwortende_kein_belegter_bedarf(self):
        ok, grund = dive.ist_luecke(["marketplace", "marketplace", "marketplace"])
        self.assertFalse(ok)


class Entdoppeln(unittest.TestCase):
    def test_umlaut_und_wortstellung(self):
        self.assertEqual(dive.normform("Fußpflege"), dive.normform("fusspflege"))
        self.assertEqual(dive.normform("fusspflege app"), dive.normform("app fuer fusspflege"))
        self.assertNotEqual(dive.normform("dienstplan app"), dive.normform("dienstplan"))

    def test_marktgroesse_wird_nicht_verdoppelt(self):
        m = [km("fusspflege", vol=4400, cpc=16.31), km("fußpflege", vol=4400, cpc=16.31),
             km("fusspflege app", vol=590, cpc=9.1), km("app für fusspflege", vol=590, cpc=9.1)]
        g = dive.monetarisierung(m)
        self.assertEqual(g["volumen_gesamt"], 4990)     # nicht 9980
        self.assertEqual(g["keywords_entdoppelt"], 2)


class Seitenzuordnung(unittest.TestCase):
    """Der Cache schreibt Schluessel klein. Wer mit der Original-URL
    nachschlaegt, verfehlt jede prozentkodierte Adresse -- App-Store- und
    YouTube-Treffer galten dadurch als unbekannt und trieben den
    unknown-Anteil auf 44 Prozent."""

    def test_grossschreibung_in_der_url(self):
        serp = {"organics": [{"rank": 1, "domain": "apps.apple.com",
                              "url": "https://apps.apple.com/de/app/Fu%C3%9Fpflege/id1",
                              "title": "t"}],
                "related": [], "paa": [], "ads": 0}
        pages = {"https://apps.apple.com/de/app/fu%c3%9fpflege/id1":
                 {"kind": "marketplace", "confidence": 0.95}}
        l = dive.serp_landschaft(serp and {"k": serp}, pages)
        self.assertEqual(l["kinds"].get("marketplace"), 1)
        self.assertNotIn("unknown", l["kinds"])


class Vergleich(unittest.TestCase):
    def test_marken_werden_erkannt(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("# Kommentar\nlexoffice\nSevDesk\n\n")
            pfad = fh.name
        self.assertEqual(dive.load_brands(pfad), {"lexoffice", "sevdesk"})

    def test_fehlende_markendatei_ist_kein_fehler(self):
        self.assertEqual(dive.load_brands("/gibt/es/nicht.txt"), set())


class Rendern(unittest.TestCase):
    """Zellen mit Farbcodes.

    Diese Tests erzwingen Farben. In der Pipe ist isatty() falsch, die
    Codes sind leer und der Fehler existiert nicht -- er trat nur im echten
    Terminal auf: naives s[:w] zerschnitt eine Escape-Sequenz, das Terminal
    frass das erste Zeichen der naechsten Spalte ('#####ROD' statt 'PROD').
    """

    def setUp(self):
        self._alt = (render.DIM, render.BOLD, render.RST)
        render.DIM, render.BOLD, render.RST = "\033[2m", "\033[1m", "\033[0m"

    def tearDown(self):
        render.DIM, render.BOLD, render.RST = self._alt

    def test_sichtbare_laenge_ignoriert_farbcodes(self):
        self.assertEqual(render.vis_len("\033[2mabc\033[0m"), 3)

    def test_kuerzen_zerschneidet_keine_sequenz(self):
        gekuerzt = render.fit(render.bar(66, 100, 8), 8)
        self.assertEqual(render.vis_len(gekuerzt), 8)
        self.assertNotRegex(gekuerzt, r"\033\[[0-9;]*$")   # kein Fragment am Ende

    def test_spalten_bleiben_ausgerichtet(self):
        rows = [{"a": "x", "b": render.bar(v, 100, 8), "c": "PROD"}
                for v in (5, 45, 95)]
        cols = [("a", "A", 4, "l"), ("b", "", 8, "l"), ("c", "C", 5, "l")]
        zeilen = render.table(rows, cols, 40).splitlines()
        breiten = set(render.vis_len(z) for z in zeilen)
        self.assertEqual(len(breiten), 1, "Zeilen unterschiedlich breit: %s" % breiten)

    def test_inhalt_bleibt_lesbar(self):
        rows = [{"b": render.bar(66, 100, 8), "c": "PROD"}]
        cols = [("b", "", 8, "l"), ("c", "SERP", 4, "l")]
        klartext = re.sub(r"\033\[[0-9;]*m", "", render.table(rows, cols, 30))
        self.assertIn("PROD", klartext)


class Rescore(unittest.TestCase):
    """Rohsignale werden gespeichert, damit Gewichtsaenderungen ohne Abruf
    wirken. Wenn das nicht stimmt, war das Speichern sinnlos."""

    def test_leitet_aus_rohsignalen_neu_ab(self):
        v = {"kind": "article", "confidence": 0.75, "why": [],
             "scores": {"product": 2, "article": 6}, "lang": None,
             "published": None, "download": False, "source": "seite"}
        staerker = rescore(dict(v, scores={"product": 8, "article": 6}))
        self.assertEqual(staerker["kind"], "product")

    def test_override_bleibt_unangetastet(self):
        v = {"kind": "product", "confidence": 1.0, "why": ["override: x"],
             "scores": {}, "lang": None, "published": None,
             "download": False, "source": "override"}
        self.assertEqual(rescore(v), v)

    def test_ohne_rohsignale_unveraendert(self):
        v = {"kind": "unknown", "confidence": 0.0, "why": ["keine Signale"],
             "scores": {}, "lang": None, "published": None,
             "download": False, "source": "seite"}
        self.assertEqual(rescore(v), v)


class Doku(unittest.TestCase):
    """Verhindert Doku-Drift: jedes in der README versprochene Flag muss es
    geben. '--product-serp-only' war beim CLI-Neubau still verschwunden und
    stand trotzdem noch in der Anleitung."""

    FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]{2,}")

    def _commands(self):
        """Aus dem Parser ableiten statt pflegen -- eine handgefuehrte Liste
        veraltet bei jedem neuen Kommando, und genau das ist dreimal
        passiert."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                main(["--help"])
        m = re.search(r"\{([a-z,]+)\}", buf.getvalue())
        self.assertIsNotNone(m, "Kommandoliste nicht im Hilfetext gefunden")
        return tuple(m.group(1).split(","))

    def test_alle_kommandos_gefunden(self):
        cmds = self._commands()
        self.assertIn("scout", cmds)
        self.assertGreaterEqual(len(cmds), 8)

    def _known_flags(self):
        known = set()
        for cmd in self._commands():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit):
                    main([cmd, "--help"])
            known |= set(self.FLAG.findall(buf.getvalue()))
        return known

    def test_versprochene_flags_existieren(self):
        readme = (Path(__file__).parent.parent / "README.md").read_text()
        # Zeilen ueber fremde Werkzeuge ausklammern: 'git commit --no-verify'
        # ist kein Keyworld-Flag und soll den Abgleich nicht ausloesen.
        eigen = [l for l in readme.splitlines()
                 if not re.search(r"\b(git|pip|python|cp|curl)\b", l)]
        promised = set(self.FLAG.findall("\n".join(eigen)))
        fehlend = sorted(promised - self._known_flags())
        self.assertEqual(fehlend, [],
                         "in README versprochen, aber nicht im CLI: %s" % fehlend)


if __name__ == "__main__":
    unittest.main()


class Produkttyp(unittest.TestCase):
    def test_regeln_und_vorrang(self):
        from keyworld.typen import typ_aus_regeln
        for kw, want in [("zeiterfassung app", "software"),
                         ("angebot erstellen vorlage", "vorlage"),
                         ("dienstplan erstellen", "rechner"),
                         ("logo erstellen lassen", "dienst"),
                         ("was ist inventur", "wissen"),
                         ("nebenkostenabrechnung frist", "wissen"),
                         ("shopify erfahrungen", "unklar")]:
            self.assertEqual(typ_aus_regeln(kw), want, kw)

    def test_gespeichert_nur_fuer_unklare(self):
        from keyworld.typen import typ
        gesp = {"shopify erfahrungen": "software", "zeiterfassung app": "wissen"}
        self.assertEqual(typ("shopify erfahrungen", gesp), "software")
        # eine Regel schlaegt eine gespeicherte Einordnung
        self.assertEqual(typ("zeiterfassung app", gesp), "software")


class Sprachmodell(unittest.TestCase):
    def test_kosten_stosszeit_und_rest(self):
        from datetime import datetime, timezone
        from keyworld.llm import kosten
        usage = {"prompt_cache_hit_tokens": 1000, "prompt_cache_miss_tokens": 4000,
                 "completion_tokens": 3000}
        peak = datetime(2026, 11, 17, 7, 0, tzinfo=timezone.utc).timestamp()   # Dienstag
        off = datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc).timestamp()    # Samstag
        voll = (1000 * 0.044 + 4000 * 1.32 + 3000 * 3.96) / 1e6
        self.assertAlmostEqual(kosten("deepseek-v4-pro", usage, peak), voll, places=9)
        self.assertAlmostEqual(kosten("deepseek-v4-pro", usage, off), voll / 2, places=9)

    def test_ohne_cache_felder(self):
        from keyworld.llm import kosten
        usage = {"prompt_tokens": 2000, "completion_tokens": 100}
        self.assertGreater(kosten("deepseek-flash", usage), 0)

    def test_anfrage_und_fehler(self):
        from unittest import mock
        from keyworld.llm import DeepSeek, LLMFehler
        antwort = mock.Mock(status_code=200)
        antwort.json.return_value = {"choices": [{"message": {"content": " {\"a\": 1} "}}],
                                     "usage": {"total_tokens": 5}, "model": "deepseek-v4-pro"}
        with mock.patch("keyworld.llm.requests.post", return_value=antwort) as post:
            text, usage, model = DeepSeek(api_key="x").chat(
                [{"role": "user", "content": "json"}], model="deepseek-v4-pro", json_mode=True)
        body = __import__("json").loads(post.call_args.kwargs["data"])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(text, '{"a": 1}')
        leer = mock.Mock(status_code=402, text="")
        leer.json.return_value = {}
        with mock.patch("keyworld.llm.requests.post", return_value=leer):
            with self.assertRaises(LLMFehler) as ctx:
                DeepSeek(api_key="x").chat([])
        self.assertIn("Guthaben", str(ctx.exception))
        with self.assertRaises(LLMFehler):
            DeepSeek(api_key="").chat([])


class Einschaetzung(unittest.TestCase):
    def setUp(self):
        import tempfile
        from keyworld.config import Config
        self._alt = Config.db_path
        self._tmp = tempfile.TemporaryDirectory()
        Config.db_path = Path(self._tmp.name) / "t.db"

    def tearDown(self):
        from keyworld.config import Config
        Config.db_path = self._alt
        self._tmp.cleanup()

    def test_parse_robust(self):
        from keyworld.einschaetzung import parse_einschaetzung
        from keyworld.llm import LLMFehler
        e = parse_einschaetzung('Vorspann {"urteil": "Lohnt sich.", "ton": 3, '
                                '"abschnitte": [{"titel": "Die Zahlen", "text": "x"}, {"titel": "leer"}]}')
        self.assertEqual(e["urteil"], "Lohnt sich")
        self.assertEqual(e["ton"], 1.0)
        self.assertEqual(len(e["abschnitte"]), 1)
        for kaputt in ["", "   ", "kein json", '{"ton": 0.5}']:
            with self.assertRaises((LLMFehler, ValueError)):
                parse_einschaetzung(kaputt)

    def test_einschaetzen_und_nachfragen_speichern(self):
        import json as _json
        from keyworld.einschaetzung import einschaetzen, kontext, nachfragen, verlauf
        from keyworld.store import Run

        class FalschesModell:
            def __init__(self):
                self.antworten = [
                    ("   ", {"total_tokens": 10, "completion_tokens": 10}, "m"),
                    (_json.dumps({"urteil": "Kleine Lücke", "ton": 0.8, "abschnitte": [
                        {"titel": "Die Zahlen", "text": "590 Suchen"}]}),
                     {"total_tokens": 100, "completion_tokens": 50}, "m"),
                    ("Weil niemand verkauft.", {"total_tokens": 20, "completion_tokens": 5}, "m")]
                self.nachrichten = []

            def chat(self, messages, **kw):
                self.nachrichten.append(messages)
                return self.antworten.pop(0)

        with Run(seed="test") as run:
            run.cache_put("dataforseo", "test vorlage", {"keyword": "test vorlage",
                          "search_volume": 590, "cpc": 2.91, "monthly": []}, market="m/de")
            run.commit()
            k = kontext(run.conn, "m/de", "test vorlage")
            self.assertTrue(k["gemessen"])
            self.assertEqual(k["typ"], "vorlage")
            llm = FalschesModell()
            v = einschaetzen(run.conn, "m/de", "Test Vorlage", run, llm, "deepseek-flash")
            self.assertEqual(v[-1]["inhalt"]["urteil"], "Kleine Lücke")
            v = nachfragen(run.conn, "m/de", "test vorlage", "Warum?", run, llm, "deepseek-flash")
            # die Nachfrage sieht die erste Einschaetzung
            self.assertIn("Kleine Lücke", " ".join(m["content"] for m in llm.nachrichten[-1]))
            self.assertEqual([m["art"] for m in verlauf(run.conn, "m/de", "test vorlage")],
                             ["start", "einschaetzung", "frage", "antwort"])
            self.assertEqual(len(run.breakdown()), 1)       # alle drei Aufrufe gebucht


class Guthaben(unittest.TestCase):
    def test_kontostand_lesen(self):
        from unittest import mock
        from keyworld.llm import DeepSeek
        r = mock.Mock(status_code=200)
        r.json.return_value = {"is_available": True, "balance_infos": [
            {"currency": "USD", "total_balance": "4.20", "granted_balance": "0.00",
             "topped_up_balance": "4.20"}]}
        with mock.patch("keyworld.llm.requests.get", return_value=r) as get:
            g = DeepSeek(api_key="x").guthaben()
        self.assertTrue(get.call_args.args[0].endswith("/user/balance"))
        self.assertTrue(g["verfuegbar"])
        self.assertEqual(g["salden"][0]["gesamt"], 4.2)
