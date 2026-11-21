"""Connection session: opens the port, talks to the car and keeps the state shown by the web dashboard.

One thread (the "worker") owns the port. Everything the dashboard asks for (read codes, clear codes, terminal)
goes through a queue and runs on that thread, between the live reads.
"""
from __future__ import annotations

import concurrent.futures as cf
import copy
import os
import queue
import re
import sys
import threading
import time
import traceback
from collections import deque
from typing import Callable, Dict, List, Optional

from . import pids as K
from . import protocol as P
from .elm327 import (BusError, ELM327, ELMDisconnected, ELMError, ELMTimeout, NegativeResponse, NoData,
                     UnableToConnect)
from .i18n import BadRequest, Conflict, Msg, NotConnected, as_msg, msg
from .kb import KnowledgeBase
from .obd import OBD
from .ports import auto_candidates, list_ports
from .simulator import SCENARIOS, make_sim_serial
from .store import History
from .transport import RfcommSocket, TransportError

# Default text for every connection state (the detail may be replaced by something more specific).
STATUS_KEY = {
    "disconnected": "st.disconnected", "opening": "st.opening", "initializing": "st.init_adapter",
    "searching": "st.searching", "online": "st.online_plain", "car_offline": "st.car_offline",
    "reconnecting": "st.reconnecting_plain", "error": "st.error",
}
SAFE_AT = {"I", "@1", "@2", "RV", "DP", "DPN", "IGN"}
SAFE_MODES = {"01", "02", "03", "07", "09", "0A"}
DEFAULT_PIDS = (0x0C, 0x0D, 0x05, 0x11, 0x04, 0x0B, 0x0F, 0x0E, 0x06, 0x07, 0x42)


class Cancelled(Exception):
    pass


def friendly_serial_error(dev: str, err: Exception) -> Msg:
    """Turn a raw pyserial / socket error into a message with a hint (Windows and Linux)."""
    s = str(err)
    low = s.lower()
    win_codes = re.findall(r"None, (\d+)\)|(\d+)\)\s*'?$", s)
    code = None
    for a, b in win_codes:
        code = int(a or b)
    errno_ = getattr(err, "errno", None)
    if (code in (2, 3) or errno_ == 2 or "filenotfound" in low or "no such file" in low or "cannot find" in low
            or "não pode encontrar" in low):
        return msg("err.port_missing", dev=dev)
    if "permission denied" in low or errno_ == 13 and sys.platform != "win32":
        return msg("err.port_perm_linux", dev=dev)
    if code == 5 or "access is denied" in low or "acesso negado" in low or "permission" in low:
        return msg("err.port_denied", dev=dev)
    if errno_ == 16 or "busy" in low:
        return msg("err.port_busy", dev=dev)
    if (code in (121, 1167, 1460) or "semaphore" in low or "semáforo" in low or "não está conectado" in low
            or "not connected" in low or errno_ in (110, 111, 112, 113, 115) or "timed out" in low or "refused" in low
            or "host is down" in low or "no route" in low):
        return msg("err.port_timeout", dev=dev)
    return msg("err.port_generic", dev=dev, err=s)


class Session:
    def __init__(self, data_dir: str, kb: KnowledgeBase):
        self.kb = kb
        self.history = History(os.path.join(data_dir, "history"))
        self.log_dir = os.path.join(data_dir, "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._gen = 0
        self._thread: Optional[threading.Thread] = None
        self._jobs: "queue.Queue" = queue.Queue()
        self._events: deque = deque(maxlen=300)
        self._raw: deque = deque(maxlen=500)
        self._seq = 0
        self._reset_state()

    # ----------------------------------------------------------------- state
    def _reset_state(self) -> None:
        self.state: dict = {
            "status": "disconnected", "detail": msg("st.disconnected"), "port": None, "demo": False,
            "scenario": None, "gen": self._gen, "car": self.kb.car_label,
            "adapter": {"version": None, "voltage": None},
            "vehicle": {"protocol": None, "vin": None, "cal_id": None, "obd_standard": None},
            "mil": {"on": None, "count": None, "ts": None},
            "monitors": [], "live": {}, "supported": [], "dtc": None, "dtc_stale": False,
            "busy": None, "connected_at": None,
        }

    def _set(self, gen: int, status: str, detail: Optional[Msg] = None) -> None:
        with self._lock:
            if gen != self._gen:
                return
            changed = self.state["status"] != status or (detail is not None and detail != self.state["detail"])
            self.state["status"] = status
            self.state["detail"] = detail or msg(STATUS_KEY[status])
            text = self.state["detail"]
        if changed:
            self._event(msg("evt.state", text=text))

    def snapshot(self, since: Optional[int] = None) -> dict:
        with self._lock:
            s = copy.deepcopy(self.state)
            s["now"] = time.time()
            s["seq"] = self._seq
            ev = [e for e in self._events if since is None or e["seq"] > since]
            s["events"] = ev[-80:] if since is None else ev
        return s

    def _event(self, m, level: str = "info") -> None:
        m = as_msg(m)
        with self._lock:
            self._seq += 1
            self._events.append({"seq": self._seq, "t": time.time(), "level": level, "msg": m})
        try:
            with open(os.path.join(self.log_dir, "obd2dash.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + f"[{level}] {m}\n")
        except OSError:
            pass

    def _rawlog(self, direction: str, text: str) -> None:
        with self._lock:
            self._raw.append({"t": time.time(), "d": direction, "x": text})

    def raw_log(self, n: int = 200) -> List[dict]:
        with self._lock:
            return list(self._raw)[-n:]

    # ------------------------------------------------------------ connection
    def connect(self, port: str = "auto", baud: Optional[int] = None, demo: bool = False,
                scenario: str = "check_engine", car: Optional[str] = None) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive() and self.state["status"] not in ("error", "disconnected"):
                raise Conflict(msg("err.already_connecting"))
            if demo and scenario not in SCENARIOS:
                scenario = "check_engine"
            if car is not None:
                self.kb.set_car(car)
            self._gen += 1
            gen = self._gen
            self._drain_jobs(msg("err.restarted"))
            self._reset_state()
            self.state["port"] = "Simulator" if demo else (port or "auto")
            self.state["demo"] = bool(demo)
            self.state["scenario"] = scenario if demo else None
            self.state["status"] = "opening"
            self.state["detail"] = msg("st.opening")
        spec = {"port": port, "baud": baud, "demo": demo, "scenario": scenario}
        self._event(msg("evt.demo", name=SCENARIOS[scenario]) if demo else msg("evt.connecting", port=port or "auto"))
        self._thread = threading.Thread(target=self._worker, args=(gen, spec), name="obd-worker", daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        with self._lock:
            self._gen += 1
            self._drain_jobs(msg("err.disconnected"))
            self._reset_state()
        self._event(msg("evt.disconnected"))

    def _drain_jobs(self, reason: Msg) -> None:
        while True:
            try:
                _fn, fut, _label = self._jobs.get_nowait()
            except queue.Empty:
                return
            if not fut.done():
                fut.set_exception(NotConnected(reason))

    # ---------------------------------------------------------------- worker
    def _open_serial(self, dev: str, baud: int):
        import serial
        try:
            return serial.Serial(port=dev, baudrate=baud, timeout=0.1, write_timeout=2)
        except (serial.SerialException, OSError, ValueError) as e:
            raise ELMError(friendly_serial_error(dev, e)) from e

    def _open_bluetooth(self, mac: str, channel: int):
        try:
            return RfcommSocket.connect(mac, channel)
        except TransportError as e:
            raise ELMError(e.msg) from e
        except OSError as e:
            raise ELMError(friendly_serial_error("bt://" + mac, e)) from e

    def _open_and_init(self, gen: int, dev: str, baud: Optional[int], quick: bool = False):
        if dev.startswith("bt://"):
            mac = dev[5:].split("/")[0]
            tries = [(f"RFCOMM ch. {ch}", (lambda ch=ch: self._open_bluetooth(mac, ch))) for ch in (1, 2, 3)]
        else:
            bauds = [baud] if baud else ([38400] if quick else [38400, 115200, 9600])
            tries = [(f"{b} baud", (lambda b=b: self._open_serial(dev, b))) for b in bauds]
        last: Optional[Exception] = None
        for i, (how, opener) in enumerate(tries):
            if gen != self._gen:
                raise Cancelled()
            self._set(gen, "opening", msg("st.opening_dev", dev=dev))
            ser = None
            for attempt in (1, 2):          # the first access to a Bluetooth port sometimes fails while the link comes up
                try:
                    ser = opener()
                    break
                except ELMError as e:
                    last = e
                    if attempt == 2 or quick or gen != self._gen:
                        break
                    self._event(msg("evt.open_retry", err=e.msg), "warn")
                    time.sleep(2)
            if ser is None:
                continue                      # next baud rate / RFCOMM channel
            elm = ELM327(ser, log=self._event, raw_log=self._rawlog)
            try:
                self._set(gen, "initializing", msg("st.init_dev", dev=dev, how=how))
                elm.initialize(attempts=1 if (quick or i > 0) else 2, atz_timeout=2.5 if quick else 4.0)
                return ser, elm
            except ELMError as e:
                last = e
                elm.close()
        raise last or ELMError(msg("err.adapter_silent"))

    def _open(self, gen: int, spec: dict):
        if spec["demo"]:
            self._set(gen, "initializing", msg("st.init_sim"))
            ser = make_sim_serial(spec["scenario"], latency=1.0)
            elm = ELM327(ser, log=self._event, raw_log=self._rawlog)
            elm.initialize()
            return ser, elm, "Simulator"
        port = spec["port"]
        if port in (None, "", "auto"):
            self._set(gen, "opening", msg("st.scanning"))
            ports = list_ports()
            cands = auto_candidates(ports)
            if not cands:
                others = ", ".join(p["device"] for p in ports) or "—"
                key = "err.no_port_win" if sys.platform == "win32" else "err.no_port_linux"
                raise ELMError(msg(key, ports=others))
            self._event(msg("evt.candidates", ports=", ".join(cands)))
            last: Optional[Exception] = None
            for dev in cands:
                kind = next((p.get("kind") for p in ports if p["device"] == dev), "")
                try:
                    ser, elm = self._open_and_init(gen, dev, spec["baud"], quick=(kind in ("usb", "serial")))
                    return ser, elm, dev
                except Cancelled:
                    raise
                except ELMError as e:
                    last = e
                    self._event(msg("evt.port_failed", dev=dev, err=e.msg), "warn")
            raise ELMError(msg("err.no_port_answered", err=last.msg if isinstance(last, ELMError) else str(last)))
        ser, elm = self._open_and_init(gen, port, spec["baud"])
        return ser, elm, port

    def _worker(self, gen: int, spec: dict) -> None:
        attempts = 0
        while gen == self._gen:
            try:
                ser, elm, label = self._open(gen, spec)
                if gen != self._gen:
                    elm.close()
                    return
                with self._lock:
                    self.state["port"] = label
                    self.state["adapter"]["version"] = elm.version
                attempts = 0
                self._session_loop(gen, elm, OBD(elm))
                elm.close()
                return
            except Cancelled:
                return
            except ELMDisconnected as e:
                attempts += 1
                self._event(msg("evt.conn_lost", err=e.msg), "warn")
                if attempts > 6 or spec["demo"]:
                    self._set(gen, "error", msg("st.lost", err=e.msg))
                    break
                self._set(gen, "reconnecting", msg("st.reconnecting", n=attempts))
                time.sleep(3)
            except ELMError as e:
                self._event(e.msg, "error")
                self._dump_raw()
                self._set(gen, "error", e.msg)
                break
            except Exception as e:  # programming error: log it and show it
                self._event(msg("st.unexpected", err=f"{type(e).__name__}: {e}"), "error")
                self._event(traceback.format_exc(), "error")
                self._set(gen, "error", msg("st.unexpected", err=f"{type(e).__name__}: {e}"))
                break
        self._drain_jobs(msg("err.conn_ended"))

    def _dump_raw(self) -> None:
        for r in self.raw_log(40):
            self._event(f"  {r['d']} {r['x']}", "debug")

    # ------------------------------------------------------------------ loop
    def _session_loop(self, gen: int, elm: ELM327, obd: OBD) -> None:
        online = False
        probe_at = 0.0
        nodata_streak = timeouts = 0
        last: Dict[object, float] = {}
        skip_until: Dict[int, float] = {}
        fails: Dict[int, int] = {}
        live_pids: List[K.PID] = []
        prev_count: Optional[int] = None
        self._set(gen, "searching")

        def go_offline() -> float:
            nonlocal online
            online = False
            self._set(gen, "car_offline")
            return time.monotonic() + 2.0

        while gen == self._gen:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                job = None
            if job:
                self._run_job(gen, job, obd)
                prev_count = (self.state["mil"] or {}).get("count")     # the job (read / clear) already updated the count
                continue
            now = time.monotonic()
            if not online:
                if now < probe_at:
                    if now - last.get("volt", 0.0) > 3.0:
                        self._poll_voltage(elm, last)
                    time.sleep(0.1)
                    continue
                try:
                    self._set(gen, "searching")
                    elm.probe_vehicle(timeout=35.0)
                except (UnableToConnect, NoData, BusError, ELMTimeout):
                    probe_at = go_offline()
                    continue
                online = True
                nodata_streak = timeouts = 0
                live_pids = self._on_online(gen, elm, obd)
                prev_count = (self.state["mil"] or {}).get("count")
                continue

            # ----- live reads: the most overdue sensor goes first
            tasks = [(last.get(p.pid, 0.0) + p.interval, p) for p in live_pids if skip_until.get(p.pid, 0.0) <= now]
            tasks.append((last.get("volt", 0.0) + 3.0, "volt"))
            tasks.append((last.get("mil", 0.0) + 4.0, "mil"))
            due, task = min(tasks, key=lambda t: t[0])
            if due > now + 0.02:
                time.sleep(min(0.1, due - now))
                continue
            try:
                if task == "volt":
                    self._poll_voltage(elm, last)
                elif task == "mil":
                    last["mil"] = time.monotonic()
                    mil = obd.read_mil_status()
                    self._set_mil(mil)
                    if prev_count is not None and mil["count"] != prev_count:
                        self._event(msg("evt.count_changed", a=prev_count, b=mil["count"]), "warn")
                        self._read_dtcs(obd, "reading", save=mil["count"] > prev_count)
                    prev_count = mil["count"]
                else:
                    p = task  # type: ignore[assignment]
                    last[p.pid] = time.monotonic()
                    raw = obd.read_pid_raw(p.pid)
                    value = p.fn(raw)
                    with self._lock:
                        self.state["live"][p.key] = {"v": value, "t": time.time()}
                    fails[p.pid] = 0
                nodata_streak = timeouts = 0
            except NoData:
                nodata_streak += 1
                if isinstance(task, K.PID):
                    fails[task.pid] = fails.get(task.pid, 0) + 1
                    if fails[task.pid] >= 3:
                        skip_until[task.pid] = time.monotonic() + 120
                        self._event(msg("evt.pid_paused", pid=f"{task.pid:02X}", label=task.label), "warn")
                if nodata_streak >= 8:
                    self._event(msg("evt.car_stopped"), "warn")
                    probe_at = go_offline()
            except NegativeResponse:
                if isinstance(task, K.PID):
                    skip_until[task.pid] = time.monotonic() + 600
            except (UnableToConnect, BusError):
                probe_at = go_offline()
            except ELMTimeout:
                timeouts += 1
                if timeouts >= 3:
                    self._event(msg("evt.adapter_hung"), "warn")
                    try:
                        elm.initialize(attempts=2)
                    except ELMError as e:
                        raise ELMDisconnected(e.msg) from e
                    online, probe_at, timeouts = False, 0.0, 0
            except ValueError:
                if isinstance(task, K.PID):
                    skip_until[task.pid] = time.monotonic() + 600

    def _poll_voltage(self, elm: ELM327, last: dict) -> None:
        last["volt"] = time.monotonic()
        try:
            v = elm.read_voltage()
        except ELMTimeout:
            return
        if v is not None:
            with self._lock:
                self.state["adapter"]["voltage"] = v

    def _set_mil(self, mil: dict) -> None:
        with self._lock:
            self.state["mil"] = {"on": mil["mil"], "count": mil["count"], "ts": time.time()}
            self.state["monitors"] = mil["monitors"]
            d = self.state.get("dtc")
            self.state["dtc_stale"] = bool(d and d.get("mil") and d["mil"]["count"] != mil["count"])

    # ------------------------------------------------------- when going online
    def _on_online(self, gen: int, elm: ELM327, obd: OBD) -> List[K.PID]:
        proto = P.protocol_name(elm.protocol)
        with self._lock:
            self.state["vehicle"]["protocol"] = proto
            self.state["connected_at"] = time.time()
            self.state["busy"] = msg("busy.info")
        self._set(gen, "online", msg("st.online", protocol=proto))
        self._event(msg("evt.protocol", protocol=proto))
        live: List[K.PID] = []
        try:
            try:
                sup = obd.supported_pids()
                live = [K.PID_BY_NUM[n] for n in sorted(sup) if n in K.PID_BY_NUM and K.PID_BY_NUM[n].live]
                self._event(msg("evt.pids", n=len(sup), live=len(live)))
            except ELMDisconnected:
                raise
            except ELMError as e:
                self._event(msg("evt.pids_fail", err=e.msg), "warn")
            if not live:     # default list: every PID without an answer pauses itself
                live = [K.PID_BY_NUM[n] for n in DEFAULT_PIDS]
                self._event(msg("evt.default_pids"), "warn")
            with self._lock:
                self.state["supported"] = [p.key for p in live]
            try:
                vin, cal, std = obd.read_vin(), obd.read_calibration_id(), obd.read_obd_standard()
                with self._lock:
                    self.state["vehicle"].update(vin=vin, cal_id=cal, obd_standard=std)
            except ELMDisconnected:
                raise
            except ELMError as e:
                self._event(msg("evt.id_fail", err=e.msg), "warn")
            with self._lock:
                self.state["busy"] = msg("busy.codes")
            try:
                self._read_dtcs(obd, "reading", save=True)
            except ELMDisconnected:
                raise
            except ELMError as e:
                self._event(msg("evt.read_fail", err=e.msg), "warn")
        finally:
            with self._lock:
                self.state["busy"] = None
        return live

    # ----------------------------------------------------------------- jobs
    def submit(self, fn: Callable[[OBD], object], timeout: float = 90.0, label: Optional[Msg] = None):
        """Run fn(obd) on the connection thread and return its result (or raise its exception)."""
        with self._lock:
            ok = self._thread is not None and self._thread.is_alive() and self.state["status"] not in ("error", "disconnected")
        if not ok:
            raise NotConnected(msg("err.not_connected"))
        fut: cf.Future = cf.Future()
        self._jobs.put((fn, fut, label))
        return fut.result(timeout=timeout)

    def _run_job(self, gen: int, job, obd: OBD) -> None:
        fn, fut, label = job
        if not fut.set_running_or_notify_cancel():
            return
        with self._lock:
            self.state["busy"] = label
        try:
            fut.set_result(fn(obd))
        except BaseException as e:  # noqa: BLE001 (handed to the caller)
            fut.set_exception(e)
            if isinstance(e, ELMDisconnected):
                raise
        finally:
            with self._lock:
                self.state["busy"] = None

    # ------------------------------------------------------- reading codes
    def _enrich(self, items: List[dict]) -> List[dict]:
        return [{"code": i["code"], "ecu": i["ecu"], "kb": self.kb.lookup(i["code"])} for i in items]

    def _read_dtcs(self, obd: OBD, event: str = "reading", save: bool = True) -> dict:
        res: dict = {"ts": time.time(), "ts_text": time.strftime("%Y-%m-%d %H:%M:%S"), "warnings": []}
        mil = None
        try:
            mil = obd.read_mil_status()
            res["mil"] = {"on": mil["mil"], "count": mil["count"]}
            res["monitors"] = mil["monitors"]
            self._set_mil(mil)
        except (NoData, NegativeResponse) as e:
            res["mil"], res["monitors"] = None, []
            res["warnings"].append(msg("warn.mil_unreadable", err=e.msg))
        try:
            stored = obd.read_dtcs(0x03)
        except NegativeResponse as e:
            stored = []
            res["warnings"].append(msg("warn.stored_refused", reason=K.nrc_msg(e.nrc)))
        try:
            pending = obd.read_dtcs(0x07)
        except NegativeResponse:
            pending = []
        permanent: Optional[List[dict]]
        try:
            permanent = obd.read_dtcs(0x0A)
            res["permanent_supported"] = True
        except (NegativeResponse, NoData):
            permanent, res["permanent_supported"] = None, False
        freeze = None
        if stored or (mil and mil["mil"]):
            try:
                freeze = obd.read_freeze_frame()
            except (ELMTimeout, NoData, NegativeResponse):
                freeze = None
        res["stored"], res["pending"] = self._enrich(stored), self._enrich(pending)
        res["permanent"] = self._enrich(permanent) if permanent is not None else []
        res["freeze"] = freeze
        all_codes = [c["code"] for c in stored + pending + (permanent or [])]
        res["tips"] = self.kb.tips(all_codes)
        if mil and mil["mil"] and not stored:
            res["warnings"].append(msg("warn.mil_no_codes"))
        if mil and not mil["mil"] and stored:
            res["warnings"].append(msg("warn.codes_mil_off"))
        with self._lock:
            self.state["dtc"] = res
            self.state["dtc_stale"] = False
        self._event(msg("evt.dtc_read", stored=",".join(c["code"] for c in stored) or "-",
                        pending=",".join(c["code"] for c in pending) or "-"))
        if save:
            try:
                self.history.save(event, res, demo=bool(self.state.get("demo")))
            except OSError as e:
                self._event(msg("evt.history_fail", err=str(e)), "warn")
        return res

    def read_dtcs_job(self) -> dict:
        return self.submit(lambda obd: self._read_dtcs(obd, "reading", True), label=msg("busy.codes"))

    def clear_job(self) -> dict:
        def run(obd: OBD) -> dict:
            before = self._read_dtcs(obd, "before_clear", True)
            codes_before = [c["code"] for c in before["stored"]]
            self._event(msg("evt.clearing", codes=",".join(codes_before) or "-"), "warn")
            try:
                obd.clear_dtcs()
            except NegativeResponse as e:
                reason = K.nrc_msg(e.nrc)
                self._event(msg("evt.clear_refused", reason=reason), "error")
                return {"ok": False, "reason": reason, "nrc": e.nrc, "before": codes_before}
            except NoData:
                return {"ok": False, "reason": msg("err.clear_no_ack"), "before": codes_before}
            time.sleep(1.5)
            after = self._read_dtcs(obd, "after_clear", True)
            remaining = [c["code"] for c in after["stored"]]
            light_off = bool(after.get("mil")) and not after["mil"]["on"]
            self._event(msg("evt.cleared_off" if light_off else "evt.cleared_on"), "warn")
            return {"ok": True, "light_off": light_off, "before": codes_before, "remaining": remaining, "after": after}

        return self.submit(run, timeout=120.0, label=msg("busy.clearing"))

    def terminal(self, cmd: str) -> dict:
        c = re.sub(r"\s+", "", cmd or "").upper()
        if not c or len(c) > 24 or not re.fullmatch(r"[0-9A-Z@]+", c):
            raise BadRequest(msg("err.term_invalid"))
        if c.startswith("AT"):
            if c[2:] not in SAFE_AT:
                raise BadRequest(msg("err.term_at", allowed=", ".join("AT" + a for a in sorted(SAFE_AT))))
        elif not re.fullmatch(r"[0-9A-F]+", c) or c[:2] not in SAFE_MODES:
            raise BadRequest(msg("err.term_modes"))

        def run(obd: OBD) -> dict:
            try:
                lines = obd.elm.command(c, 10.0)
            except ELMTimeout as e:
                return {"cmd": c, "lines": [], "error": e.msg}
            return {"cmd": c, "lines": lines}

        return self.submit(run, timeout=30.0, label=msg("busy.command", cmd=c))
