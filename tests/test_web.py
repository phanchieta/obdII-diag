"""Browser side: the two UI dictionaries stay in sync, every key used by the HTML/JS exists (and none is dead),
and the static files are served with the session token injected."""
import http.client
import json
import os
import re
import shutil
import tempfile
import threading
import unittest

import obd2dash  # noqa: F401
from obd2dash import ROOT
from obd2dash.server import App, create_server

WEB = os.path.join(ROOT, "web")
I18N_DIR = os.path.join(WEB, "i18n")
JS_FILES = ("app.js", "gauges.js", "i18n.js")
KEY_LITERAL = re.compile(r"""['"]([a-z][a-z0-9]*\.[a-z0-9_]+)['"]""")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _read(*parts):
    with open(os.path.join(WEB, *parts), encoding="utf-8") as f:
        return f.read()


def _dict(lang):
    with open(os.path.join(I18N_DIR, lang + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _base(key):
    return re.sub(r"_(one|other)$", "", key)


def _html_keys(html):
    keys = set(re.findall(r'data-i18n(?:-html)?="([^"]+)"', html))
    for attr in re.findall(r'data-i18n-attr="([^"]+)"', html):
        for pair in attr.split(";"):
            if ":" in pair:
                keys.add(pair.split(":", 1)[1].strip())
    return keys


def _js_keys():
    keys = set()
    for name in JS_FILES:
        keys.update(KEY_LITERAL.findall(_read(name)))
    return keys


class DictionaryTests(unittest.TestCase):
    def test_same_keys_in_both_languages(self):
        pt, en = _dict("pt"), _dict("en")
        self.assertEqual(sorted(set(pt) - set(en)), [], "keys only in pt.json")
        self.assertEqual(sorted(set(en) - set(pt)), [], "keys only in en.json")

    def test_same_placeholders(self):
        pt, en = _dict("pt"), _dict("en")
        for k in en:
            self.assertEqual(sorted(PLACEHOLDER.findall(pt[k])), sorted(PLACEHOLDER.findall(en[k])), k)

    def test_plurals_come_in_pairs(self):
        for lang in ("pt", "en"):
            d = _dict(lang)
            for k in d:
                if k.endswith("_one"):
                    self.assertIn(_base(k) + "_other", d, f"{lang}: {k}")
                if k.endswith("_other"):
                    self.assertIn(_base(k) + "_one", d, f"{lang}: {k}")

    def test_no_empty_or_untranslated_values(self):
        pt, en = _dict("pt"), _dict("en")
        for k in en:
            self.assertTrue(en[k].strip(), "empty en: " + k)
            self.assertTrue(pt[k].strip(), "empty pt: " + k)

    def test_html_only_in_html_slots(self):
        """Values holding markup may only be used through data-i18n-html (everything else is inserted as text)."""
        html = _read("index.html")
        html_slots = set(re.findall(r'data-i18n-html="([^"]+)"', html))
        for lang in ("pt", "en"):
            for k, v in _dict(lang).items():
                if "<" in v:
                    self.assertIn(k, html_slots, f"{lang}: '{k}' has markup but is not used with data-i18n-html")


class KeyUsageTests(unittest.TestCase):
    def test_every_used_key_exists(self):
        en = _dict("en")
        used = _html_keys(_read("index.html")) | _js_keys()
        missing = []
        for k in sorted(used):
            if k in en or (k + "_one" in en and k + "_other" in en):
                continue
            missing.append(k)
        self.assertEqual(missing, [], "keys used by the HTML/JS but absent from the dictionaries")

    def test_no_dead_keys(self):
        used = {_base(k) for k in _html_keys(_read("index.html")) | _js_keys()}
        dead = sorted(k for k in _dict("en") if _base(k) not in used and k not in used)
        self.assertEqual(dead, [], "dictionary keys that nothing references")

    def test_html_ids_used_by_app_exist(self):
        html = _read("index.html")
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        app = _read("app.js")
        wanted = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'", app)) | set(re.findall(r"\$\$\('#([A-Za-z0-9_-]+)", app))
        # these are created by the script itself, not present in the static HTML
        created = {"mini-lamp", "mini-lamp-t", "mini-lamp-s", "clr-ok", "cn-sc", "cn-car", "cn-prog", "cn-msg", "raw-box"}
        self.assertEqual(sorted(wanted - ids - created), [], "ids queried by app.js that index.html does not define")


class StaticServingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="obd2dash-web-")
        cls.app = App(data_dir=cls.tmp, car_label="")
        cls.server = create_server(cls.app, 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.stopping = True
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        ctype = r.getheader("Content-Type", "")
        c.close()
        return r.status, ctype, body

    def test_index_gets_the_token_and_version(self):
        status, ctype, body = self.get("/")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn('content="%s"' % self.app.token, text)
        self.assertNotIn("__TOKEN__", text)
        self.assertNotIn("__VERSION__", text)

    def test_assets_and_dictionaries_are_served(self):
        for path, expect in (("/app.js", "javascript"), ("/gauges.js", "javascript"), ("/i18n.js", "javascript"),
                             ("/style.css", "text/css"), ("/favicon.svg", "image/svg+xml"),
                             ("/i18n/pt.json", "json"), ("/i18n/en.json", "json")):
            status, ctype, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(expect, ctype, path)
            self.assertTrue(body, path)
        status, _, body = self.get("/i18n/en.json")
        self.assertEqual(json.loads(body.decode("utf-8")), _dict("en"))

    def test_token_is_not_in_static_assets(self):
        for path in ("/app.js", "/i18n.js", "/i18n/en.json"):
            self.assertNotIn(self.app.token.encode(), self.get(path)[2], path)

    def test_flag_buttons_exist_for_each_language(self):
        html = _read("index.html")
        for lang in ("pt", "en"):
            self.assertRegex(html, r'class="lang-btn"[^>]*data-lang="%s"|data-lang="%s"[^>]*class="lang-btn"' % (lang, lang))

    def test_directory_traversal_is_refused(self):
        self.assertEqual(self.get("/..%2f..%2fREADME.md")[0], 404)
        self.assertEqual(self.get("/i18n/../../obd2dash/server.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
