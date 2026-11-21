import glob
import os
import re
import string
import unittest

import obd2dash  # noqa: F401
from obd2dash import pids as K
from obd2dash.i18n import FALLBACK, LANGS, Msg, literal, msg, pick, tr
from obd2dash.kb import KnowledgeBase
from obd2dash.messages import CATALOGS
from obd2dash.simulator import SCENARIOS

PKG = os.path.join(os.path.dirname(obd2dash.__file__))
KEY = re.compile(r"""["']((?:st|err|evt|log|busy|warn|http|rep)\.[a-z_0-9.]+)["']""")


def placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class Catalogues(unittest.TestCase):
    def test_same_keys_in_every_language(self):
        base = set(CATALOGS[FALLBACK])
        for lang in LANGS:
            self.assertEqual(set(CATALOGS[lang]), base, f"{lang} catalogue differs from {FALLBACK}")

    def test_no_empty_text_and_same_placeholders(self):
        for key, en in CATALOGS[FALLBACK].items():
            for lang in LANGS:
                text = CATALOGS[lang][key]
                self.assertTrue(text.strip(), f"{lang}:{key} is empty")
                self.assertEqual(placeholders(text), placeholders(en), f"placeholders differ in {lang}:{key}")

    def test_every_key_used_in_the_code_exists(self):
        used = set()
        for path in glob.glob(os.path.join(PKG, "*.py")):
            if path.endswith("messages.py"):
                continue
            with open(path, encoding="utf-8") as f:
                used.update(KEY.findall(f.read()))
        self.assertGreater(len(used), 60)
        missing = sorted(k for k in used if k not in CATALOGS[FALLBACK])
        self.assertEqual(missing, [])

    def test_unused_keys_are_reported(self):
        used = set()
        for path in glob.glob(os.path.join(PKG, "*.py")):
            if path.endswith("messages.py"):
                continue
            with open(path, encoding="utf-8") as f:
                used.update(KEY.findall(f.read()))
        unused = sorted(k for k in CATALOGS[FALLBACK] if k not in used)
        self.assertEqual(unused, [], "catalogue keys that no code uses")


class Helpers(unittest.TestCase):
    def test_msg_renders_all_languages(self):
        m = msg("err.timeout", cmd="010C", secs="3")
        self.assertEqual(set(m), set(LANGS))
        self.assertIn("010C", m["en"])
        self.assertIn("Sem resposta", m["pt"])
        self.assertEqual(str(m), m["en"])

    def test_params_can_be_messages(self):
        inner = msg("st.disconnected")
        m = msg("evt.state", text=inner)
        self.assertEqual(m["en"], "State: Disconnected")
        self.assertEqual(m["pt"], "Estado: Desconectado")

    def test_pick_and_literal(self):
        self.assertEqual(pick(Msg(pt="a", en="b"), "pt"), "a")
        self.assertEqual(pick(Msg(pt="a", en="b"), "xx"), "b")
        self.assertEqual(pick("plain", "pt"), "plain")
        self.assertEqual(pick(None, "pt"), "")
        self.assertEqual(literal("x"), {"pt": "x", "en": "x"})
        self.assertEqual(tr("st.disconnected", "pt"), "Desconectado")


class DataTables(unittest.TestCase):
    def test_pid_labels_and_enums_have_every_language(self):
        for p in K.PIDS:
            for lang in LANGS:
                self.assertTrue(p.label[lang].strip(), f"PID {p.key} has no {lang} label")
        for table in (K.FUEL_SYSTEM, K.FUEL_TYPES, K.OBD_STANDARDS, K.NRC_TEXT):
            for m in table.values():
                self.assertEqual(set(m), set(LANGS))

    def test_scenarios_have_every_language(self):
        for key, m in SCENARIOS.items():
            self.assertEqual(set(m), set(LANGS), key)


class KnowledgeBaseParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = KnowledgeBase("Honda Fit 2011")

    def test_curated_files_mirror_each_other(self):
        base = self.kb.curated[FALLBACK]
        self.assertGreaterEqual(len(base), 100)
        for lang in LANGS:
            other = self.kb.curated[lang]
            self.assertEqual(sorted(other), sorted(base), f"{lang} has different codes than {FALLBACK}")
            for code, e in base.items():
                o = other[code]
                self.assertEqual(o["severity"], e["severity"], f"{lang}:{code} severity")
                self.assertEqual(len(o["c"]), len(e["c"]), f"{lang}:{code} number of causes")
                self.assertEqual(len(o["a"]), len(e["a"]), f"{lang}:{code} number of actions")
                self.assertEqual(bool(o["r"]), bool(e["r"]), f"{lang}:{code} can-drive text")
                self.assertEqual(bool(o["h"]), bool(e["h"]), f"{lang}:{code} brand tip")
                self.assertEqual(bool(o["e"]), bool(e["e"]), f"{lang}:{code} explanation")
                self.assertEqual(o["system"].startswith("Honda"), e["system"].startswith("Honda"), f"{lang}:{code} brand")

    def test_every_entry_is_complete(self):
        for lang in LANGS:
            for code, e in self.kb.curated[lang].items():
                self.assertRegex(code, r"^[PCBU][0-9A-F]{4}$")
                self.assertTrue(e["title"] and e["system"], code)
                self.assertIn(e["severity"], (1, 2, 3, 4), code)
                self.assertTrue(e["c"] and e["a"], f"{lang}:{code} needs causes and actions")
                self.assertNotIn(";", e["title"])

    def test_every_lookup_has_every_language(self):
        for code in ("P0420", "P0302", "P0133", "P0113", "P0202", "P0262", "P0352", "P0752", "P0A0F", "P1999", "U0100", "P1456"):
            r = self.kb.lookup(code)
            self.assertEqual(set(r["t"]), set(LANGS), code)
            for lang in LANGS:
                t = r["t"][lang]
                for field in ("title", "system", "severity_text", "can_drive", "what", "scope", "search_url"):
                    self.assertTrue(t[field], f"{code}/{lang}/{field}")


if __name__ == "__main__":
    unittest.main()
