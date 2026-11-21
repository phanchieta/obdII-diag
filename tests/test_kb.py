import unittest

import obd2dash  # noqa: F401
from obd2dash.kb import KnowledgeBase


class KB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = KnowledgeBase("Honda Fit 2011")

    def test_key_codes_for_the_fit_exist(self):
        for code in ("P0420", "P0171", "P0172", "P0300", "P0128", "P0456", "P0457", "P0442", "P1456", "P1457",
                     "P1259", "P2646", "P0505", "P0562", "U0100", "P0335", "P0340", "P0217"):
            self.assertIn(code, self.kb.curated["en"], code)
            self.assertIn(code, self.kb.curated["pt"], code)

    def test_lookup_curated(self):
        r = self.kb.lookup("P0420")
        self.assertIn("catalisador", r["t"]["pt"]["title"].lower())
        self.assertIn("catalyst", r["t"]["en"]["title"].lower())
        self.assertEqual(r["severity"], 2)
        self.assertEqual(r["source"], "curated")
        for lang in ("pt", "en"):
            t = r["t"][lang]
            self.assertTrue(t["causes"] and t["actions"] and t["can_drive"] and t["what"])
            self.assertIn("google.com", t["search_url"])
            self.assertIn("P0420", t["search_url"])
            self.assertIn("Honda", t["search_url"])
        self.assertTrue(r["generic"])
        self.assertTrue(r["t"]["en"]["scope"].startswith("Generic"))
        self.assertEqual(r["t"]["en"]["severity_text"], "Medium")
        self.assertEqual(r["t"]["pt"]["severity_text"], "Média")

    def test_default_text_when_missing(self):
        r = self.kb.lookup("P0125")
        self.assertTrue(r["t"]["en"]["what"])
        r = self.kb.lookup("P0227")      # not curated: falls back to the SAE title / generic text
        self.assertTrue(r["t"]["pt"]["what"])

    def test_brand_tips_only_for_honda(self):
        honda = self.kb.lookup("P0420")["t"]["en"]["brand_tip"]
        self.assertTrue(honda)
        other = KnowledgeBase("Toyota Corolla")
        self.assertEqual(other.lookup("P0420")["t"]["en"]["brand_tip"], "")
        self.assertEqual(other.lookup("P0420")["brand"], "")
        self.assertEqual(self.kb.lookup("P0420")["brand"], "Honda")

    def test_honda_p1_codes_are_hidden_for_other_brands(self):
        self.assertEqual(self.kb.lookup("P1456")["source"], "curated")
        other = KnowledgeBase("")
        r = other.lookup("P1456")
        self.assertEqual(r["source"], "generic")
        self.assertFalse(r["generic"])
        self.assertIn("anufacturer", r["t"]["en"]["scope"])
        other.set_car("Honda Civic")
        self.assertEqual(other.lookup("P1456")["source"], "curated")

    def test_families(self):
        en = lambda c: self.kb.lookup(c)["t"]["en"]["title"]   # noqa: E731
        pt = lambda c: self.kb.lookup(c)["t"]["pt"]["title"]   # noqa: E731
        self.assertEqual(en("P0302"), "Misfire on cylinder 2")
        self.assertEqual(pt("P0302"), "Falha de ignição no cilindro 2")
        self.assertEqual(en("P0312"), "Misfire on cylinder 12")
        self.assertEqual(self.kb.lookup("P0302")["severity"], 3)
        self.assertIn("bank 1 sensor 1", en("P0133").lower())
        self.assertIn("slow response", en("P0133").lower())
        self.assertIn("banco 1 sensor 1", pt("P0133").lower())
        self.assertIn("resposta lenta", pt("P0133").lower())
        self.assertIn("bank 1 sensor 2", en("P0141").lower())
        self.assertIn("heater", en("P0141").lower())
        self.assertIn("bank 2 sensor 1", en("P0151").lower())
        self.assertEqual(en("P0113"), "Intake air temperature (IAT) sensor: high signal")
        self.assertEqual(pt("P0113"), "Sensor de temperatura do ar admitido (IAT): sinal alto")
        self.assertIn("low signal", en("P0117"))
        self.assertIn("MAF", en("P0101"))
        self.assertEqual(en("P0202"), "Cylinder 2 injector circuit")
        self.assertEqual(en("P0262"), "Cylinder 1 injector: high signal")
        self.assertEqual(en("P0267"), "Cylinder 3 injector: low signal")
        self.assertEqual(en("P0270"), "Cylinder 4 injector: low signal")
        self.assertEqual(en("P0352"), "Ignition coil B primary/secondary circuit")
        self.assertEqual(en("P0752"), "Shift solenoid A: stuck on")
        self.assertIn("A: preso ligado", pt("P0752"))

    def test_misfire_family_uses_the_base_guidance(self):
        a = self.kb.lookup("P0303")["t"]["en"]["actions"]
        self.assertIn("cylinder 3", a[0])
        self.assertGreaterEqual(len(a), 5)

    def test_sae_fallback_and_generic(self):
        r = self.kb.lookup("P0A0F")
        self.assertIn(r["source"], ("sae", "generic"))
        if r["source"] == "sae":
            self.assertTrue(r["t"]["pt"]["note"])
            self.assertEqual(r["t"]["en"]["note"].count("SAE") >= 1, True)
        r = self.kb.lookup("P1999")
        self.assertEqual(r["source"], "generic")
        self.assertTrue(r["t"]["en"]["actions"])
        self.assertIn("anufacturer", r["t"]["en"]["scope"])

    def test_every_generic_code_resolves(self):
        for code in ("P0001", "P0A10", "P2A00", "U0402", "P3000", "C0035", "B1000", "U2100"):
            r = self.kb.lookup(code)
            for lang in ("pt", "en"):
                self.assertTrue(r["t"][lang]["title"] and r["t"][lang]["what"], code)

    def test_tips_are_bilingual(self):
        t = self.kb.tips(["P0301", "P0420"])
        self.assertTrue(any("ignição" in x["pt"] and "catalisador" in x["pt"] for x in t))
        self.assertTrue(any("misfire" in x["en"] and "catalytic" in x["en"] for x in t))
        self.assertTrue(any("fuel cap" in x["en"] for x in self.kb.tips(["P0456"])))
        self.assertTrue(any("battery" in x["en"].lower() for x in self.kb.tips(["U0100"])))
        self.assertEqual(self.kb.tips([]), [])


if __name__ == "__main__":
    unittest.main()
