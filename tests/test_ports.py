import socket
import types
import unittest
from unittest import mock

import obd2dash  # noqa: F401
from obd2dash import ports
from obd2dash.elm327 import ELM327
from obd2dash.obd import OBD
from obd2dash.session import friendly_serial_error
from obd2dash.simulator import make_sim_serial
from obd2dash.store import make_report
from obd2dash.transport import RfcommSocket


class FriendlyErrors(unittest.TestCase):
    def test_windows_access_denied(self):
        e = Exception("could not open port 'COM5': PermissionError(13, 'Access is denied.', None, 5)")
        self.assertIn("access denied", friendly_serial_error("COM5", e)["en"].lower())
        e = Exception("could not open port 'COM5': PermissionError(13, 'Acesso negado.', None, 5)")
        self.assertIn("acesso negado", friendly_serial_error("COM5", e)["pt"].lower())

    def test_windows_missing_port(self):
        e = Exception("could not open port 'COM7': FileNotFoundError(2, 'O sistema não pode encontrar o arquivo especificado.', None, 2)")
        self.assertIn("does not exist", friendly_serial_error("COM7", e)["en"])
        self.assertIn("não existe", friendly_serial_error("COM7", e)["pt"])

    def test_windows_semaphore_timeout(self):
        e = Exception("could not open port 'COM5': OSError(None, 'O tempo limite do semáforo expirou.', None, 121)")
        self.assertIn("timed out", friendly_serial_error("COM5", e)["en"])

    def test_linux_errors(self):
        e = Exception("[Errno 13] could not open port /dev/ttyUSB0: [Errno 13] Permission denied: '/dev/ttyUSB0'")
        self.assertIn("dialout", friendly_serial_error("/dev/ttyUSB0", e)["en"])
        e = Exception("[Errno 2] could not open port /dev/ttyUSB1: [Errno 2] No such file or directory: '/dev/ttyUSB1'")
        self.assertIn("does not exist", friendly_serial_error("/dev/ttyUSB1", e)["en"])
        e = OSError(111, "Connection refused")
        self.assertIn("timed out", friendly_serial_error("bt://AA", e)["en"])

    def test_unknown(self):
        self.assertIn("COM1", friendly_serial_error("COM1", Exception("something odd"))["en"])


FAKE = [
    types.SimpleNamespace(device="COM3", description="Standard Serial over Bluetooth link (COM3)",
                          hwid=r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000\7&2A6B42D7&0&000000000000_00000000"),
    types.SimpleNamespace(device="COM4", description="Standard Serial over Bluetooth link (COM4)",
                          hwid=r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_VID&00010085_PID&0014\7&2A6B42D7&0&B8D50B6D4B84_C00000000"),
    types.SimpleNamespace(device="COM10", description="Standard Serial over Bluetooth link (COM10)",
                          hwid=r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_VID&00010085_PID&0014\7&2A6B42D7&0&AABBCCDDEEFF_C00000000"),
    types.SimpleNamespace(device="COM1", description="Communications Port (COM1)", hwid="ACPI\\PNP0501\\0"),
    types.SimpleNamespace(device="COM6", description="USB Serial Port (COM6)", hwid="USB VID:PID=0403:6001 SER=A1"),
]


class PortListing(unittest.TestCase):
    def test_windows_list_and_candidates(self):
        with mock.patch("serial.tools.list_ports.comports", return_value=FAKE), \
             mock.patch.object(ports, "IS_WINDOWS", True), mock.patch.object(ports, "IS_LINUX", False), \
             mock.patch.object(ports, "bluetooth_names", return_value={"B8D50B6D4B84": "OBDII", "AABBCCDDEEFF": "Headset"}):
            lst = ports.list_ports()
        by = {p["device"]: p for p in lst}
        self.assertTrue(by["COM3"]["incoming"])
        self.assertFalse(by["COM4"]["incoming"])
        self.assertEqual(by["COM4"]["bt_name"], "OBDII")
        self.assertEqual(by["COM4"]["kind"], "bluetooth")
        self.assertFalse(by["COM1"]["bluetooth"])
        self.assertEqual(by["COM6"]["kind"], "usb")
        self.assertEqual([p["device"] for p in lst], ["COM1", "COM3", "COM4", "COM6", "COM10"])    # natural order
        # adapter-like names first, no incoming port, USB serial adapters last
        self.assertEqual(ports.auto_candidates(lst), ["COM4", "COM10", "COM6"])

    def test_candidates_fallback_when_only_unknown_bt(self):
        lst = [{"device": "COM8", "bluetooth": True, "incoming": True, "bt_name": None}]
        self.assertEqual(ports.auto_candidates(lst), ["COM8"])
        self.assertEqual(ports.auto_candidates([{"device": "COM1", "bluetooth": False, "incoming": False, "kind": "serial"}]), [])

    def test_linux_bluetooth_devices_are_listed_as_rfcomm_sockets(self):
        out = "Device AA:BB:CC:DD:EE:01 OBDII\nDevice 11:22:33:44:55:66 JBL Flip 5\n"
        with mock.patch("serial.tools.list_ports.comports", return_value=[]), \
             mock.patch.object(ports, "IS_WINDOWS", False), mock.patch.object(ports, "IS_LINUX", True), \
             mock.patch.object(ports, "bluetooth_sockets_available", return_value=True), \
             mock.patch.object(ports, "_run", return_value=out):
            lst = ports.list_ports()
        self.assertEqual([p["device"] for p in lst], ["bt://AA:BB:CC:DD:EE:01", "bt://11:22:33:44:55:66"])
        self.assertEqual(lst[0]["bt_name"], "OBDII")
        self.assertEqual(ports.auto_candidates(lst)[0], "bt://AA:BB:CC:DD:EE:01")      # OBD-like name first

    def test_linux_without_bluez_or_sockets(self):
        with mock.patch("serial.tools.list_ports.comports", return_value=[]), \
             mock.patch.object(ports, "IS_WINDOWS", False), mock.patch.object(ports, "IS_LINUX", True), \
             mock.patch.object(ports, "bluetooth_sockets_available", return_value=False):
            self.assertEqual(ports.list_ports(), [])
        with mock.patch.object(ports, "_run", return_value=""):
            self.assertEqual(ports.linux_paired_bluetooth(), [])

    def test_real_enumeration_does_not_crash(self):
        self.assertIsInstance(ports.list_ports(with_names=False), list)


class RfcommTransport(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair()
        self.t = RfcommSocket(self.a, timeout=0.05)
        self.addCleanup(self.t.close)
        self.addCleanup(self.b.close)

    def test_read_write_and_in_waiting(self):
        self.assertEqual(self.t.in_waiting, 0)
        self.assertEqual(self.t.read(1), b"")                 # timeout, not an error
        self.b.sendall(b"ELM327 v1.5\r\r>")
        self.assertGreater(self.t.in_waiting, 0)
        self.assertEqual(self.t.read(self.t.in_waiting), b"ELM327 v1.5\r\r>")
        self.assertEqual(self.t.write(b"ATZ\r"), 4)
        self.assertEqual(self.b.recv(16), b"ATZ\r")

    def test_peer_closing_raises(self):
        self.b.close()
        with self.assertRaises(OSError):
            self.t.in_waiting
        with self.assertRaises(OSError):
            self.t.read(1)

    def test_closed_port_raises(self):
        self.t.close()
        self.assertFalse(self.t.is_open)
        with self.assertRaises(OSError):
            self.t.write(b"x")

    def test_elm327_driver_talks_over_the_socket(self):
        """The ELM327 driver only needs read/write/in_waiting: run a tiny fake adapter on the other end."""
        import threading
        from obd2dash.simulator import ELM327Sim, SimCar

        sim = ELM327Sim(SimCar("check_engine"), latency=0)

        def adapter():
            buf = b""
            self.b.settimeout(2)
            try:
                while True:
                    data = self.b.recv(256)
                    if not data:
                        return
                    buf += data
                    while b"\r" in buf:
                        line, buf = buf.split(b"\r", 1)
                        text, _ = sim.process(line.decode())
                        if text:
                            self.b.sendall(text.encode("latin-1"))
            except OSError:
                return

        threading.Thread(target=adapter, daemon=True).start()
        elm = ELM327(self.t, timeout=2.0)
        elm.initialize()
        elm.probe_vehicle()
        self.assertEqual(elm.protocol, "6")
        self.assertEqual([d["code"] for d in OBD(elm).read_dtcs(0x03)], ["P0420", "P0171"])


class CountSuffixAndReport(unittest.TestCase):
    def test_report_text_in_both_languages(self):
        from obd2dash.kb import KnowledgeBase
        kb = KnowledgeBase("Honda Fit 2011")
        c = {"code": "P0420", "ecu": "7E8", "kb": kb.lookup("P0420")}
        state = {"adapter": {"version": "ELM327 v1.5", "voltage": 12.4}, "vehicle": {"protocol": "CAN", "vin": "VIN123"},
                 "live": {"rpm": {"v": 1000.0, "t": 1}, "fuel_status": {"v": {"pt": "Malha fechada", "en": "Closed loop"}, "t": 1}},
                 "dtc": {"mil": {"on": True, "count": 1}, "stored": [c], "pending": [], "permanent": [], "permanent_supported": False,
                         "freeze": {"dtc": "P0420", "values": [{"label": {"pt": "Rotação do motor", "en": "Engine speed"},
                                                                  "value": 2210, "unit": "rpm", "dec": 0}]},
                         "monitors": [{"name": {"pt": "Catalisador", "en": "Catalyst"}, "ready": False}],
                         "tips": [{"pt": "dica x", "en": "tip x"}], "warnings": []}}
        en = make_report(state, "Honda Fit 2011", "en")
        for s in ("OBD-II REPORT — Honda Fit 2011", "ON", "P0420", "STORED CODES", "FREEZE-FRAME", "Engine speed: 2210 rpm",
                  "Catalyst: NOT ready", "not supported", "TIP: tip x", "Engine speed: 1000 rpm", "Closed loop", "severity: Medium"):
            self.assertIn(s, en)
        pt = make_report(state, "Honda Fit 2011", "pt")
        for s in ("RELATÓRIO OBD2 — Honda Fit 2011", "ACESA", "CÓDIGOS ARMAZENADOS", "DADOS CONGELADOS", "Rotação do motor: 2210 rpm",
                  "Catalisador: NÃO pronto", "DICA: dica x", "Malha fechada", "gravidade: Média", "12,4 V"):
            self.assertIn(s, pt)
        self.assertIn("github.com/phanchieta/obdII-diag", en)

    def test_report_without_reading(self):
        self.assertIn("no trouble-code reading", make_report({"adapter": {}, "vehicle": {}, "dtc": None}, "", "en"))
        self.assertIn("nenhuma leitura", make_report({"adapter": {}, "vehicle": {}, "dtc": None}, "", "pt"))


if __name__ == "__main__":
    unittest.main()
