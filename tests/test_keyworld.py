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

    COMMANDS = ("scout", "gen", "review", "queue", "costs", "serve",
                "discover", "rescore", "fetch", "probe", "dive")
    FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]{2,}")

    def _known_flags(self):
        known = set()
        for cmd in self.COMMANDS:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit):
                    main([cmd, "--help"])
            known |= set(self.FLAG.findall(buf.getvalue()))
        return known

    def test_versprochene_flags_existieren(self):
        readme = (Path(__file__).parent.parent / "README.md").read_text()
        promised = set(self.FLAG.findall(readme))
        fehlend = sorted(promised - self._known_flags())
        self.assertEqual(fehlend, [],
                         "in README versprochen, aber nicht im CLI: %s" % fehlend)


if __name__ == "__main__":
    unittest.main()
