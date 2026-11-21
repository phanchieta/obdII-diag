import unittest

import obd2dash  # noqa: F401  (sets up sys.path)
from obd2dash import pids as K
from obd2dash import protocol as P
from obd2dash.elm327 import (ELM327, ELMDisconnected, ELMTimeout, NegativeResponse, NoData, UnableToConnect)
from obd2dash.obd import OBD
from obd2dash.simulator import ELM327Sim, SimCar, SimSerial, make_sim_serial


class StatusKind(unittest.TestCase):
    def test_kinds(self):
        self.assertIsNone(P.status_kind("7E8 06 41 00 BE 3F A8 13"))
        self.assertIsNone(P.status_kind("0: 49 02 01"))
        self.assertEqual(P.status_kind("SEARCHING..."), "searching")
        self.assertEqual(P.status_kind("NO DATA"), "nodata")
        self.assertEqual(P.status_kind("UNABLE TO CONNECT"), "unable")
        self.assertEqual(P.status_kind("BUS INIT: ...ERROR"), "businit_error")
        self.assertEqual(P.status_kind("BUS INIT: OK"), "businit")
        self.assertEqual(P.status_kind("CAN ERROR"), "canerror")
        self.assertEqual(P.status_kind("?"), "unknown_cmd")
        self.assertEqual(P.status_kind("OK"), "ok")
        self.assertEqual(P.status_kind("ELM327 v1.5"), "other")


class ParseResponse(unittest.TestCase):
    def test_can_single_frame(self):
        r = P.parse_response(["SEARCHING...", "7E8 06 41 00 BE 3F A8 13 00"], "6")
        self.assertEqual(len(r.messages), 1)
        self.assertEqual(r.messages[0].ecu, "7E8")
        self.assertEqual(r.messages[0].data, bytes.fromhex("4100BE3FA813"))
        self.assertIn("searching", r.status)

    def test_can_multiframe_vin(self):
        lines = ["7E8 10 14 49 02 01 39 33 48", "7E8 21 47 45 38 44 45 4D 4F", "7E8 22 30 30 30 30 30 30 31"]
        r = P.parse_response(lines, "6")
        self.assertEqual(r.messages[0].data[:3], bytes([0x49, 0x02, 0x01]))
        self.assertEqual(r.messages[0].data[3:].decode(), "93HGE8DEMO0000001")

    def test_no_spaces(self):
        r = P.parse_response(["7E8064100BE3FA81300"], "6")
        self.assertEqual(r.messages[0].data, bytes.fromhex("4100BE3FA813"))

    def test_headers_off_single_and_multiline(self):
        r = P.parse_response(["41 0C 1A F8"], "6", headers=False)
        self.assertEqual(r.messages[0].data, bytes.fromhex("410C1AF8"))
        r = P.parse_response(["014", "0: 49 02 01 39 33 48", "1: 47 45 38 44 45 4D 4F", "2: 30 30 30 30 30 30 31"],
                             "6", headers=False)
        self.assertEqual(len(r.messages), 1)
        self.assertEqual(r.messages[0].data[3:].decode(), "93HGE8DEMO0000001")

    def test_kline(self):
        r = P.parse_response(["48 6B 11 41 0C 1A F8 3A"], "3")
        self.assertEqual(r.messages[0].data, bytes.fromhex("410C1AF8"))
        self.assertEqual(r.messages[0].ecu, "11")

    def test_can29(self):
        r = P.parse_response(["18 DA F1 10 06 41 00 BE 3F A8 13"], "7")
        self.assertEqual(r.messages[0].ecu, "18DAF110")
        self.assertEqual(r.messages[0].data, bytes.fromhex("4100BE3FA813"))

    def test_pci_already_stripped(self):
        r = P.parse_response(["7E8 41 0C 1A F8"], "6")
        self.assertEqual(r.messages[0].data, bytes.fromhex("410C1AF8"))

    def test_two_ecus(self):
        r = P.parse_response(["7E8 04 41 0C 1A F8", "7E9 04 41 0C 1A F0"], "6")
        self.assertEqual([m.ecu for m in r.messages], ["7E8", "7E9"])

    def test_nodata_and_garbage(self):
        r = P.parse_response(["NO DATA"], "6")
        self.assertEqual(r.messages, [])
        self.assertEqual(r.status, ["nodata"])
        r = P.parse_response(["7E8 06 41 0Z"], "6")
        self.assertEqual(r.messages, [])


class Decoders(unittest.TestCase):
    def test_pids(self):
        self.assertEqual(K.decode_pid(0x0C, bytes.fromhex("1AF8")), 1726)
        self.assertEqual(K.decode_pid(0x05, bytes([0x7B])), 83)
        self.assertEqual(K.decode_pid(0x0D, bytes([60])), 60)
        self.assertAlmostEqual(K.decode_pid(0x11, bytes([0x80])), 50.196, places=2)
        self.assertEqual(K.decode_pid(0x0E, bytes([0x90])), 8.0)
        self.assertAlmostEqual(K.decode_pid(0x06, bytes([0x80])), 0.0)
        self.assertAlmostEqual(K.decode_pid(0x42, bytes.fromhex("3732")), 14.13, places=2)
        self.assertAlmostEqual(K.decode_pid(0x24, bytes.fromhex("80001000")), 1.0)
        with self.assertRaises(ValueError):
            K.decode_pid(0x0C, bytes([0x1A]))

    def test_enum_pids_are_bilingual(self):
        fs = K.decode_pid(0x03, bytes([2, 0]))
        self.assertIn("closed loop", fs["en"].lower())
        self.assertIn("fechada", fs["pt"].lower())
        self.assertEqual(K.decode_pid(0x03, bytes([0, 0])), "—")
        self.assertEqual(K.decode_pid(0x51, bytes([3]))["en"], "Ethanol")

    def test_dtc(self):
        self.assertEqual(K.decode_dtc(0x01, 0x33), "P0133")
        self.assertEqual(K.decode_dtc(0x04, 0x20), "P0420")
        self.assertEqual(K.decode_dtc(0x41, 0x23), "C0123")
        self.assertEqual(K.decode_dtc(0x81, 0x00), "B0100")
        self.assertEqual(K.decode_dtc(0xC1, 0x00), "U0100")
        self.assertEqual(K.decode_dtc(0x16, 0x56), "P1656")
        self.assertIsNone(K.decode_dtc(0, 0))

    def test_dtc_payload(self):
        self.assertEqual(K.decode_dtc_payload(bytes.fromhex("43020420" "0171"), True), ["P0420", "P0171"])
        self.assertEqual(K.decode_dtc_payload(bytes.fromhex("4300"), True), [])
        self.assertEqual(K.decode_dtc_payload(bytes.fromhex("43" "04200171" "0000"), False), ["P0420", "P0171"])
        self.assertEqual(K.decode_dtc_payload(bytes.fromhex("43" "000000000000"), False), [])
        self.assertEqual(K.decode_dtc_payload(bytes.fromhex("430104200000000"[:-1]), True), ["P0420"])

    def test_monitors(self):
        m = K.decode_monitor_status(0x82, 0x07, 0x65, 0x00)
        self.assertTrue(m["mil"])
        self.assertEqual(m["count"], 2)
        self.assertTrue(all(x["ready"] for x in m["monitors"]))
        self.assertEqual(m["monitors"][0]["name"]["en"], "Misfire")
        self.assertIn("ignição", m["monitors"][0]["name"]["pt"])
        m = K.decode_monitor_status(0x00, 0x77, 0x65, 0x65)
        self.assertFalse(m["mil"])
        self.assertTrue(all(not x["ready"] for x in m["monitors"]))

    def test_negative_response_text(self):
        self.assertIn("ignition", K.nrc_msg(0x22)["en"])
        self.assertIn("0x99", K.nrc_msg(0x99)["en"])


def connect(scenario="check_engine", **kw):
    ser = make_sim_serial(scenario, latency=0, **kw)
    elm = ELM327(ser, timeout=2.0)
    elm.initialize()
    return elm, OBD(elm), ser


class DriverWithSimulator(unittest.TestCase):
    def test_full_flow_can(self):
        elm, obd, ser = connect()
        self.addCleanup(ser.close)
        self.assertIn("ELM327", elm.version)
        elm.probe_vehicle()
        self.assertEqual(elm.protocol, "6")
        sup = obd.supported_pids()
        for p in (0x01, 0x04, 0x05, 0x0C, 0x0D, 0x11, 0x42):
            self.assertIn(p, sup)
        self.assertGreaterEqual(elm.read_voltage(), 12.0)
        self.assertEqual(obd.read_pid(0x05) > 30, True)
        mil = obd.read_mil_status()
        self.assertTrue(mil["mil"])
        self.assertEqual(mil["count"], 2)
        self.assertEqual([d["code"] for d in obd.read_dtcs(0x03)], ["P0420", "P0171"])
        self.assertEqual([d["code"] for d in obd.read_dtcs(0x07)], ["P0456"])
        with self.assertRaises(NegativeResponse):
            obd.read_dtcs(0x0A)
        ff = obd.read_freeze_frame()
        self.assertEqual(ff["dtc"], "P0420")
        vals = {v["key"]: v["value"] for v in ff["values"]}
        self.assertEqual(vals["rpm"], 2210)
        self.assertEqual(vals["ect"], 89)
        self.assertEqual(ff["values"][0]["label"]["en"], "Engine speed")
        self.assertEqual(obd.read_vin(), SimCar.VIN)
        self.assertEqual(obd.read_calibration_id(), SimCar.CAL_ID)
        self.assertEqual(obd.read_obd_standard()["en"], "Brazil OBD phase 2 (OBDBr-2)")

    def test_clear_codes(self):
        elm, obd, ser = connect()
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        self.assertTrue(obd.clear_dtcs())
        self.assertFalse(obd.read_mil_status()["mil"])
        self.assertEqual(obd.read_dtcs(0x03), [])
        self.assertIsNone(obd.read_freeze_frame())
        mons = obd.read_mil_status()["monitors"]
        self.assertTrue(mons and all(not m["ready"] for m in mons))

    def test_clear_refused_with_engine_running(self):
        elm, obd, ser = connect(strict_clear=True)
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        with self.assertRaises(NegativeResponse) as cm:
            obd.clear_dtcs()
        self.assertEqual(cm.exception.nrc, 0x22)

    def test_no_codes_scenario(self):
        elm, obd, ser = connect("healthy")
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        self.assertFalse(obd.read_mil_status()["mil"])
        self.assertEqual(obd.read_dtcs(0x03), [])

    def test_unsupported_pid_is_nodata(self):
        elm, obd, ser = connect()
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        with self.assertRaises(NoData):
            obd.read_pid_raw(0x5C)

    def test_ignition_off(self):
        elm, obd, ser = connect("ignition_off")
        self.addCleanup(ser.close)
        with self.assertRaises(UnableToConnect):
            elm.probe_vehicle()

    def test_timeout_then_recover(self):
        elm, obd, ser = connect()
        self.addCleanup(ser.close)
        elm.timeout = 0.5
        elm.probe_vehicle()
        ser.sim.drop_next = 1
        with self.assertRaises(ELMTimeout):
            obd.read_pid(0x0C)
        self.assertGreater(obd.read_pid(0x0C), 0)

    def test_garbage_response(self):
        elm, obd, ser = connect()
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        ser.sim.garbage_next = 1
        with self.assertRaises(NoData):
            obd.read_pid(0x0C)
        self.assertGreater(obd.read_pid(0x0C), 0)

    def test_port_closed(self):
        elm, obd, ser = connect()
        elm.probe_vehicle()
        ser.close()
        with self.assertRaises(ELMDisconnected):
            obd.read_pid(0x0C)

    def test_clone_without_count_suffix(self):
        elm, obd, ser = connect(reject_count=True)
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        self.assertGreater(obd.read_pid(0x0C), 0)
        self.assertFalse(elm.count_ok)
        self.assertGreater(obd.read_pid(0x0D) + 1, 0)

    def test_clone_answering_nodata_to_count_suffix(self):
        elm, obd, ser = connect(count_nodata=True)
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        self.assertGreater(obd.read_pid(0x0C), 0)      # works through a retry without the suffix
        self.assertTrue(elm.count_ok)
        self.assertGreater(obd.read_pid(0x0D) + 1, 0)
        self.assertFalse(elm.count_ok)                  # after two occurrences the suffix is disabled
        self.assertGreater(obd.read_pid(0x05), 0)

    def test_headers_without_pci_variant(self):
        elm, obd, ser = connect(strip_pci=True)
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        self.assertGreater(obd.read_pid(0x0C), 0)
        self.assertEqual([d["code"] for d in obd.read_dtcs(0x03)], ["P0420", "P0171"])

    def test_kline_protocol(self):
        elm, obd, ser = connect(protocol="3")
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        self.assertEqual(elm.protocol, "3")
        self.assertFalse(obd.can)
        self.assertIn(0x0C, obd.supported_pids())
        self.assertGreater(obd.read_pid(0x0C), 0)
        self.assertEqual([d["code"] for d in obd.read_dtcs(0x03)], ["P0420", "P0171"])
        self.assertEqual(obd.read_vin(), SimCar.VIN)
        self.assertTrue(obd.clear_dtcs())
        self.assertEqual(obd.read_dtcs(0x03), [])

    def test_can29_protocol(self):
        elm, obd, ser = connect(protocol="7")
        self.addCleanup(ser.close)
        elm.probe_vehicle()
        self.assertEqual(elm.protocol, "7")
        self.assertGreater(obd.read_pid(0x0C), 0)
        self.assertEqual([d["code"] for d in obd.read_dtcs(0x03)], ["P0420", "P0171"])
        self.assertEqual(obd.read_vin(), SimCar.VIN)

    def test_many_codes_multiframe(self):
        car = SimCar("check_engine")
        car.stored = ["P0300", "P0301", "P0302", "P0303", "P0304", "P0420", "P0171"]
        ser = SimSerial(ELM327Sim(car, latency=0))
        self.addCleanup(ser.close)
        elm = ELM327(ser, timeout=2.0)
        elm.initialize()
        elm.probe_vehicle()
        obd = OBD(elm)
        self.assertEqual([d["code"] for d in obd.read_dtcs(0x03)], car.stored)

    def test_headers_off_driver_path(self):
        """The app still works if the adapter ignores ATH1 (headers off)."""
        ser = make_sim_serial(latency=0)
        self.addCleanup(ser.close)
        elm = ELM327(ser, timeout=2.0)
        elm.initialize()
        elm.command("ATH0")
        elm.headers = False
        elm.probe_vehicle()
        obd = OBD(elm)
        self.assertGreater(obd.read_pid(0x0C), 0)
        self.assertEqual([d["code"] for d in obd.read_dtcs(0x03)], ["P0420", "P0171"])
        self.assertEqual(obd.read_vin(), SimCar.VIN)


if __name__ == "__main__":
    unittest.main()
