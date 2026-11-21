import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

import obd2dash  # noqa: F401
from obd2dash import server as server_mod
from obd2dash import session as session_mod
from obd2dash.server import App, create_server
from obd2dash.simulator import make_sim_serial as _real_make_sim_serial


def _fast_sim(scenario, latency=1.0, **kw):
    return _real_make_sim_serial(scenario, latency=0, **kw)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig = session_mod.make_sim_serial
        session_mod.make_sim_serial = _fast_sim
        cls.tmp = tempfile.mkdtemp(prefix="obd2dash-test-")
        cls.app = App(data_dir=cls.tmp, car_label="")
        cls.server = create_server(cls.app, 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.stopping = True
        cls.app.session.disconnect()
        cls.server.shutdown()
        cls.server.server_close()
        session_mod.make_sim_serial = cls._orig
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------- helpers
    def call(self, method, path, body=None, token=True, host=None, raw=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-OBD-Token"] = self.app.token
        if host:
            headers["Host"] = host
        c.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        if raw:
            return r.status, data.decode("utf-8")
        return r.status, json.loads(data.decode("utf-8"))

    def wait(self, cond, timeout=20.0, step=0.1):
        end = time.monotonic() + timeout          # monotonic: the wall clock can jump (VM resync, NTP)
        while time.monotonic() < end:
            st = self.call("GET", "/api/state")[1]
            if cond(st):
                return st
            time.sleep(step)
        self.fail("timeout; last state: " + json.dumps(self.call("GET", "/api/state")[1])[:600])

    def connect_demo(self, scenario="check_engine", car=None):
        self.call("POST", "/api/disconnect", {})
        body = {"demo": True, "scenario": scenario}
        if car is not None:
            body["car"] = car
        code, r = self.call("POST", "/api/connect", body)
        self.assertEqual(code, 200, r)

    # --------------------------------------------------------------- tests
    def test_01_security(self):
        code, r = self.call("POST", "/api/disconnect", {}, token=False)
        self.assertEqual(code, 403)
        self.assertEqual(set(r["error"]), {"pt", "en"})
        self.assertEqual(self.call("GET", "/api/state", host="evil.example.com")[0], 403)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("GET", "/../../etc/passwd", raw=True)[0], 404)
        self.assertEqual(self.call("POST", "/api/dtc/clear", {})[0], 400)       # confirmation is mandatory

    def test_02_not_connected(self):
        self.call("POST", "/api/disconnect", {})
        code, r = self.call("POST", "/api/dtc/read", {})
        self.assertEqual(code, 409)
        self.assertIn("connect", r["error"]["en"].lower())
        self.assertIn("conexão", r["error"]["pt"].lower())

    def test_03_ports_and_pids_endpoints(self):
        code, r = self.call("GET", "/api/ports")
        self.assertEqual(code, 200)
        self.assertIsInstance(r["ports"], list)
        self.assertIn("check_engine", r["scenarios"])
        self.assertEqual(set(r["scenarios"]["check_engine"]), {"pt", "en"})
        pids_ = self.call("GET", "/api/pids")[1]["pids"]
        self.assertEqual(pids_["rpm"]["unit"], "rpm")
        self.assertEqual(pids_["rpm"]["label"]["en"], "Engine speed")
        self.assertGreater(pids_["load"]["interval"], 0)

    def test_04_kb_endpoint(self):
        code, r = self.call("GET", "/api/kb/p0420")
        self.assertEqual(code, 200)
        self.assertEqual(r["kb"]["code"], "P0420")
        self.assertIn("catalyst", r["kb"]["t"]["en"]["title"].lower())

    def test_05_diagnostics_endpoint_shape(self):
        fake = {"platform": "windows", "sections": [{"key": "radios", "lines": ["x"]}]}
        with mock.patch.object(server_mod, "bluetooth_diagnostics", return_value=fake):
            code, r = self.call("GET", "/api/diag/bluetooth")
        self.assertEqual(code, 200)
        self.assertEqual(r["sections"][0]["key"], "radios")

    def test_10_demo_flow_read_and_clear(self):
        self.connect_demo(car="Honda Fit 2011")
        st = self.wait(lambda s: s["status"] == "online" and s["dtc"] is not None and not s["busy"], 30)
        self.assertEqual(st["car"], "Honda Fit 2011")
        self.assertTrue(st["mil"]["on"])
        self.assertEqual(st["mil"]["count"], 2)
        self.assertEqual([c["code"] for c in st["dtc"]["stored"]], ["P0420", "P0171"])
        self.assertEqual([c["code"] for c in st["dtc"]["pending"]], ["P0456"])
        first = st["dtc"]["stored"][0]["kb"]
        self.assertIn("catalyst", first["t"]["en"]["title"].lower())
        self.assertIn("catalisador", first["t"]["pt"]["title"].lower())
        self.assertTrue(first["t"]["en"]["brand_tip"])                      # Honda tips enabled by the car name
        self.assertEqual(st["dtc"]["freeze"]["dtc"], "P0420")
        self.assertTrue(st["vehicle"]["vin"].startswith("93H"))
        self.assertIn("CAN", st["vehicle"]["protocol"])
        self.assertEqual(st["vehicle"]["obd_standard"]["en"], "Brazil OBD phase 2 (OBDBr-2)")
        self.assertEqual(st["detail"]["en"].split(" · ")[0], "Connected to the car")
        self.assertIn("rpm", st["supported"])
        self.assertTrue(any("catalyst" in t["en"] or "fuel cap" in t["en"] for t in st["dtc"]["tips"]))
        st = self.wait(lambda s: "rpm" in s["live"] and "ect" in s["live"] and s["adapter"]["voltage"], 20)
        self.assertGreater(st["live"]["rpm"]["v"], 0)
        st = self.wait(lambda s: "fuel_status" in s["live"], 20)
        self.assertEqual(set(st["live"]["fuel_status"]["v"]), {"pt", "en"})

        code, r = self.call("POST", "/api/dtc/read", {})
        self.assertEqual(code, 200)
        self.assertEqual(len(r["result"]["stored"]), 2)

        code, r = self.call("POST", "/api/dtc/clear", {"confirm": True})
        self.assertEqual(code, 200, r)
        res = r["result"]
        self.assertTrue(res["ok"])
        self.assertTrue(res["light_off"])
        self.assertEqual(res["before"], ["P0420", "P0171"])
        self.assertEqual(res["remaining"], [])
        st = self.wait(lambda s: s["mil"]["on"] is False and s["dtc"] and not s["dtc"]["stored"], 10)
        self.assertEqual(st["mil"]["count"], 0)

        items = self.call("GET", "/api/history")[1]["items"]
        events = [i["event"] for i in items]
        self.assertIn("before_clear", events)
        self.assertIn("after_clear", events)
        before = [i for i in items if i["event"] == "before_clear"][0]
        self.assertEqual(before["stored"], ["P0420", "P0171"])
        self.assertTrue(before["demo"])
        doc = self.call("GET", "/api/history/" + before["id"])[1]["doc"]
        self.assertEqual(doc["event"], "before_clear")
        self.assertEqual(self.call("GET", "/api/history/..%2f..%2fx")[0], 404)

        code, text = self.call("GET", "/api/report?lang=en", raw=True)
        self.assertEqual(code, 200)
        self.assertIn("OBD-II REPORT", text)
        self.assertIn("OBD-II REPORT — Honda Fit 2011", text)
        code, text = self.call("GET", "/api/report?lang=pt", raw=True)
        self.assertIn("RELATÓRIO OBD2", text)

    def test_20_terminal_restrictions(self):
        self.connect_demo()
        self.wait(lambda s: s["status"] == "online" and not s["busy"], 30)
        code, r = self.call("POST", "/api/terminal", {"cmd": "0100"})
        self.assertEqual(code, 200, r)
        self.assertTrue(any("41" in l for l in r["result"]["lines"]))
        for bad in ("04", "ATZ", "ATPP00", "2F"):
            code, r = self.call("POST", "/api/terminal", {"cmd": bad})
            self.assertEqual(code, 400, bad)
            self.assertEqual(set(r["error"]), {"pt", "en"})
        code, r = self.call("POST", "/api/terminal", {"cmd": "ATRV"})
        self.assertEqual(code, 200)
        self.assertTrue(r["result"]["lines"][0].endswith("V"))

    def test_30_ignition_off(self):
        self.connect_demo("ignition_off")
        st = self.wait(lambda s: s["status"] == "car_offline", 20)
        self.assertIn("ignition", st["detail"]["en"])
        self.assertIn("ignição", st["detail"]["pt"])
        code, r = self.call("POST", "/api/dtc/read", {})
        self.assertNotEqual(code, 200)

    def test_40_stream(self):
        self.connect_demo("healthy")
        self.wait(lambda s: s["status"] == "online" and s["dtc"] is not None, 30)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/stream")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn("text/event-stream", r.getheader("Content-Type"))
        line = r.fp.readline().decode()
        self.assertTrue(line.startswith("data: "))
        snap = json.loads(line[6:])
        self.assertEqual(snap["status"], "online")
        self.assertFalse(snap["mil"]["on"])
        c.close()

    def test_50_misfire_scenario(self):
        self.connect_demo("misfire", car="")
        st = self.wait(lambda s: s["status"] == "online" and s["dtc"] is not None and not s["busy"], 30)
        self.assertEqual([c["code"] for c in st["dtc"]["stored"]], ["P0301", "P0300", "P0420"])
        self.assertTrue(any("catalytic" in t["en"] for t in st["dtc"]["tips"]))
        kb = st["dtc"]["stored"][0]["kb"]
        self.assertEqual(kb["t"]["en"]["title"], "Misfire on cylinder 1")
        self.assertEqual(kb["t"]["pt"]["title"], "Falha de ignição no cilindro 1")
        self.assertEqual(kb["t"]["en"]["brand_tip"], "")                  # not a Honda: no brand tips

    def test_60_recurring_fault_is_detected_again(self):
        self.connect_demo("recurring_fault")
        self.wait(lambda s: s["status"] == "online" and s["dtc"] is not None and not s["busy"], 30)
        r = self.call("POST", "/api/dtc/clear", {"confirm": True})[1]["result"]
        self.assertTrue(r["light_off"])
        # the simulated fault reappears 15 s after clearing; the app notices the new code count by itself
        st = self.wait(lambda s: s["mil"]["on"] is True and s["dtc"] and len(s["dtc"]["stored"]) == 2, 40)
        self.assertEqual([c["code"] for c in st["dtc"]["stored"]], ["P0420", "P0171"])


if __name__ == "__main__":
    unittest.main()
