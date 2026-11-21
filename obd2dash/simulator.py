"""ELM327 + car simulator (a 2011 Honda Fit) for the demo mode and for the tests.

The simulator speaks the SAME text protocol as the real adapter (echo, '>' prompt, SEARCHING...,
NO DATA, CAN frames with PCI, multi-frame, K-line...), so the app runs exactly the same code path
with the simulator and with a real car.
"""
from __future__ import annotations

import math
import queue
import random
import re
import threading
import time
from typing import Callable, Dict, List, Optional

from .i18n import Msg

SCENARIOS = {
    "check_engine": Msg(pt="Luz de injeção acesa: P0420 + P0171 (armazenados) e P0456 (pendente)",
                        en="Check-engine light on: P0420 + P0171 (stored) and P0456 (pending)"),
    "misfire": Msg(pt="Falha de ignição: P0301 + P0300 + P0420", en="Misfire: P0301 + P0300 + P0420"),
    "healthy": Msg(pt="Carro saudável, sem códigos", en="Healthy car, no codes"),
    "engine_off": Msg(pt="Ignição ligada, motor desligado (com códigos)", en="Ignition on, engine off (with codes)"),
    "ignition_off": Msg(pt="Ignição desligada (adaptador sem resposta do carro)", en="Ignition off (the car does not answer the adapter)"),
    "recurring_fault": Msg(pt="Defeito que volta logo depois de apagar os códigos", en="A fault that comes back right after clearing the codes"),
}

CYCLE = [(0, 0), (8, 0), (20, 48), (34, 50), (48, 88), (70, 90), (84, 24), (92, 0), (120, 0)]
GEAR_K = {1: 150.0, 2: 88.0, 3: 60.0, 4: 44.0, 5: 34.0}


def encode_dtc(code: str) -> bytes:
    idx = "PCBU".index(code[0])
    return bytes([(idx << 6) | (int(code[1]) << 4) | int(code[2], 16), int(code[3:5], 16)])


def _interp(points, t):
    if t <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t0 <= t <= t1:
            return v1 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def _bitmap(pids, base: int) -> bytes:
    v = 0
    for p in pids:
        if base < p <= base + 32:
            v |= 1 << (32 - (p - base))
    return v.to_bytes(4, "big")


class SimCar:
    VIN = "93HGE8DEMO0000001"
    CAL_ID = "37805-RB0-DEMO"

    def __init__(self, scenario: str = "check_engine", clock: Callable[[], float] = time.monotonic,
                 seed: Optional[int] = 1, supports_0a: bool = False, strict_clear: bool = False):
        self.clock = clock
        self.rng = random.Random(seed)
        self.t0 = clock()
        self.scenario = scenario
        self.supports_0a = supports_0a
        self.strict_clear = strict_clear      # refuse to clear while the engine runs (NRC 0x22)
        self.ignition = scenario != "ignition_off"
        self.engine_on = scenario not in ("engine_off", "ignition_off")
        self.persist = scenario == "recurring_fault"
        self.stored: List[str] = []
        self.pending: List[str] = []
        self.permanent: List[str] = []
        self.freeze: Optional[dict] = None
        self.monitors_ready = True
        self.reappear_at: Optional[float] = None
        self.clear_count = 0
        self.cleared_t: Optional[float] = None
        self._apply(scenario)

    # ---------------------------------------------------------------- scenarios
    def _apply(self, scenario: str) -> None:
        self.lean = False
        if scenario in ("check_engine", "engine_off", "recurring_fault"):
            self.stored, self.pending = ["P0420", "P0171"], ["P0456"]
            self.lean = True
        elif scenario == "misfire":
            self.stored, self.pending = ["P0301", "P0300", "P0420"], []
        elif scenario == "healthy":
            self.stored, self.pending = [], []
        if self.stored:
            self.freeze = {0x02: self.stored[0], 0x04: 41.0, 0x05: 89, 0x06: 1.6, 0x07: 9.4, 0x0B: 52, 0x0C: 2210,
                           0x0D: 62, 0x0E: 28.5, 0x0F: 38, 0x11: 22.0, 0x03: 2, 0x42: 14.1, 0x43: 40.0}
        self._saved = (list(self.stored), list(self.pending), self.freeze)

    @property
    def mil(self) -> bool:
        return bool(self.stored)

    def clear(self) -> None:
        self.stored, self.pending, self.permanent, self.freeze = [], [], [], None
        self.monitors_ready = False
        self.clear_count += 1
        self.cleared_t = self.clock()
        if self.persist:
            self.reappear_at = self.clock() + 15.0

    def _tick(self) -> None:
        if self.reappear_at and self.clock() >= self.reappear_at:
            self.reappear_at = None
            self.stored, self.pending, self.freeze = list(self._saved[0]), list(self._saved[1]), self._saved[2]

    # -------------------------------------------------------------------- physics
    def snap(self) -> dict:
        self._tick()
        t = self.clock() - self.t0
        if not self.engine_on:
            return {"speed": 0, "rpm": 0, "throttle": 0.0, "load": 0.0, "map": 93, "timing": 0.0,
                    "ect": 36.0, "iat": 31.0, "stft": 0.0, "ltft": 3.1, "lambda": 1.0, "volt": 12.35, "t": t}
        c = t % 120.0
        speed = _interp(CYCLE, c)
        acc = (_interp(CYCLE, (c + 0.5) % 120) - _interp(CYCLE, (c - 0.5) % 120)) if 0.5 < c < 119.5 else 0.0
        if speed < 0.5:
            rpm, speed = 790 + 25 * math.sin(t * 3.1) + self.rng.uniform(-8, 8), 0.0
        else:
            gear = 1 if speed < 14 else 2 if speed < 28 else 3 if speed < 45 else 4 if speed < 70 else 5
            rpm = max(820.0, speed * GEAR_K[gear]) + 60 * max(acc, 0) + self.rng.uniform(-12, 12)
        throttle = max(12.0, min(85.0, 13.5 + 9 * max(acc, 0) + 0.12 * speed + (0 if speed else self.rng.uniform(0, .4))))
        load = max(17.0, min(95.0, 17 + throttle * 0.75))
        ect = 42 + (89 - 42) * (1 - math.exp(-t / 150.0))
        return {
            "speed": speed, "rpm": max(0.0, rpm), "throttle": throttle, "load": load,
            "map": min(98.0, 30 + throttle * 0.85), "timing": 10 + load * 0.28 - (acc < 0) * 6,
            "ect": ect, "iat": 33 + min(8.0, t / 60.0), "stft": 2.2 * math.sin(t * 1.3) + self.rng.uniform(-0.6, 0.6),
            "ltft": 14.8 if self.lean else 2.3, "lambda": 1.0 + 0.035 * math.sin(t * 2.1),
            "volt": 14.05 + 0.08 * math.sin(t * 0.7), "t": t,
        }

    def _monitor(self) -> bytes:
        a = (0x80 if self.mil else 0) | len(self.stored)
        b = 0x07 | (0 if self.monitors_ready else 0x70)
        c = 0x65                      # catalisador, EVAP, sensor O2, aquecedor O2
        d = 0 if self.monitors_ready else 0x65
        return bytes([a, b, c, d])

    def pid_bytes(self, pid: int) -> Optional[bytes]:
        s = self.snap()
        now = self.clock()
        run = int(s["t"]) if self.engine_on else 0
        since_clear = int(now - self.cleared_t) if self.cleared_t else 1234
        two = lambda v: int(v).to_bytes(2, "big")
        table: Dict[int, Callable[[], bytes]] = {
            0x01: self._monitor,
            0x03: lambda: bytes([2 if s["ect"] > 60 else 1, 0]),
            0x04: lambda: bytes([int(s["load"] * 255 / 100)]),
            0x05: lambda: bytes([int(s["ect"] + 40)]),
            0x06: lambda: bytes([int((s["stft"] + 100) * 128 / 100)]),
            0x07: lambda: bytes([int((s["ltft"] + 100) * 128 / 100)]),
            0x0B: lambda: bytes([int(s["map"])]),
            0x0C: lambda: two(s["rpm"] * 4),
            0x0D: lambda: bytes([int(s["speed"])]),
            0x0E: lambda: bytes([int((s["timing"] + 64) * 2) & 0xFF]),
            0x0F: lambda: bytes([int(s["iat"] + 40)]),
            0x11: lambda: bytes([int(s["throttle"] * 255 / 100)]),
            0x13: lambda: bytes([0x03]),
            0x15: lambda: bytes([int((0.45 + 0.3 * math.sin(s["t"] * 0.9)) * 200) & 0xFF, 0xFF]),
            0x1C: lambda: bytes([29]),
            0x1F: lambda: two(run),
            0x21: lambda: two(143 if self.mil else 0),
            0x24: lambda: two(s["lambda"] * 32768) + two(1.1 * 8192 * (1 + 0.1 * math.sin(s["t"]))),
            0x2E: lambda: bytes([int(15 * 255 / 100)]),
            0x2F: lambda: bytes([int((62 - s["t"] / 1500.0) * 255 / 100)]),
            0x30: lambda: bytes([min(255, since_clear // 600 + (0 if self.cleared_t else 38))]),
            0x31: lambda: two(since_clear // 30 if self.cleared_t else 987),
            0x33: lambda: bytes([93]),
            0x42: lambda: two(s["volt"] * 1000),
            0x43: lambda: two(s["load"] * 255 / 100 * 0.95),
            0x44: lambda: two(32768),
            0x46: lambda: bytes([29 + 40]),
        }
        fn = table.get(pid)
        return fn() if fn else None

    def all_pids(self) -> List[int]:
        data = [p for p in range(1, 0x80) if p % 0x20 and self.pid_bytes(p) is not None]
        # PIDs 0x20/0x40/0x60 say "more PIDs are supported in the next block"
        more = [0x20 * k for k in (1, 2, 3) if any(p > 0x20 * k for p in data)]
        return sorted(data + more)

    def ff_pids(self) -> List[int]:
        return sorted(self.freeze) if self.freeze else []

    def ff_bytes(self, pid: int) -> Optional[bytes]:
        if not self.freeze or pid not in self.freeze:
            return None
        v = self.freeze[pid]
        enc = {
            0x02: lambda: encode_dtc(v), 0x03: lambda: bytes([v, 0]), 0x04: lambda: bytes([int(v * 255 / 100)]),
            0x05: lambda: bytes([v + 40]), 0x06: lambda: bytes([int((v + 100) * 128 / 100)]),
            0x07: lambda: bytes([int((v + 100) * 128 / 100)]), 0x0B: lambda: bytes([v]),
            0x0C: lambda: int(v * 4).to_bytes(2, "big"), 0x0D: lambda: bytes([v]),
            0x0E: lambda: bytes([int((v + 64) * 2)]), 0x0F: lambda: bytes([v + 40]),
            0x11: lambda: bytes([int(v * 255 / 100)]), 0x42: lambda: int(v * 1000).to_bytes(2, "big"),
            0x43: lambda: int(v * 255 / 100).to_bytes(2, "big"),
        }
        return enc[pid]()


class Neg(Exception):
    def __init__(self, nrc):
        self.nrc = nrc


class ELM327Sim:
    """Emulates the AT commands and the response format of the ELM327."""

    def __init__(self, car: SimCar, latency: float = 1.0, protocol: str = "6", version: str = "ELM327 v1.5",
                 strip_pci: bool = False, reject_count: bool = False, count_nodata: bool = False):
        self.car = car
        self.latency = latency
        self.version = version
        self.real_protocol = protocol
        self.strip_pci = strip_pci          # headers on but no PCI byte (adapter variant)
        self.reject_count = reject_count    # clone that answers '?' to the number-of-responses suffix
        self.count_nodata = count_nodata    # clone that answers NO DATA to the suffix
        self.drop_next = 0                  # ignore N commands (simulates a timeout)
        self.garbage_next = 0               # answer garbage N times
        self.log: List[str] = []
        self.reset()

    def reset(self):
        self.echo, self.linefeed, self.spaces, self.headers, self.caf = True, True, True, False, True
        self.proto, self.found, self.last = "0", None, ""

    # -------------------------------------------------------------- formatacao
    def _eol(self) -> str:
        return "\r\n" if self.linefeed else "\r"

    def _hex(self, bs) -> str:
        return (" " if self.spaces else "").join(f"{b:02X}" for b in bs)

    def _hdr(self) -> List[int]:
        return [0x7E8] if self.found == "6" else [0x18, 0xDA, 0xF1, 0x10]

    def _can_lines(self, payload: bytes) -> List[str]:
        n = len(payload)
        frames: List[List[int]] = []
        if n <= 7:
            frames.append([n] + list(payload) + [0] * (7 - n))
        else:
            frames.append([0x10 | (n >> 8), n & 0xFF] + list(payload[:6]))
            rest, seq = payload[6:], 1
            for i in range(0, len(rest), 7):
                chunk = list(rest[i:i + 7])
                frames.append([0x20 | (seq & 0xF)] + chunk + [0] * (7 - len(chunk)))
                seq += 1
        if self.headers:
            if self.found == "6":
                head = "7E8"
            else:
                head = self._hex([0x18, 0xDA, 0xF1, 0x10])
            out = []
            for f in frames:
                body = f[1:1 + n] if (self.strip_pci and len(frames) == 1) else f
                out.append(head + (" " if self.spaces else "") + self._hex(body))
            return out
        if len(frames) == 1:
            return [self._hex(payload)]
        lines = [f"{n:03X}"]
        lines.append("0: " + self._hex(payload[:6]))
        rest = payload[6:]
        for i in range(0, len(rest), 7):
            lines.append(f"{(i // 7 + 1) & 0xF:X}: " + self._hex(rest[i:i + 7]))
        return lines

    def _kline_lines(self, payload: bytes) -> List[str]:
        msgs: List[bytes] = []
        sid = payload[0]
        if sid in (0x43, 0x47, 0x4A):                  # DTCs: 3 per message, no count byte
            body = payload[2:]
            pairs = [body[i:i + 2] for i in range(0, len(body), 2)] or [b"\x00\x00"]
            for i in range(0, len(pairs), 3):
                grp = pairs[i:i + 3]
                grp += [b"\x00\x00"] * (3 - len(grp))
                msgs.append(bytes([sid]) + b"".join(grp))
        elif sid == 0x49 and payload[1] in (0x02, 0x04):  # VIN / CAL ID: 4 bytes per message
            body = payload[3:]
            body = (b"\x00" * ((-len(body)) % 4)) + body
            for i in range(0, len(body), 4):
                msgs.append(bytes([0x49, payload[1], i // 4 + 1]) + body[i:i + 4])
        else:
            msgs.append(payload)
        out = []
        for m in msgs:
            raw = bytes([0x48 + 0, 0x6B, 0x11]) + m          # 0x48 0x6B 0x11 (ISO 9141-2)
            if self.headers:
                out.append(self._hex(raw + bytes([sum(raw) & 0xFF])))
            else:
                out.append(self._hex(m))
        return out

    def _render(self, payload: bytes) -> List[str]:
        if self.found in ("6", "7"):
            return self._can_lines(payload)
        return self._kline_lines(payload)

    # ---------------------------------------------------------------- servicos
    def _service(self, mode: int, cmd: str) -> Optional[bytes]:
        car = self.car
        if mode == 1:
            pid = int(cmd[2:4], 16)
            if pid % 0x20 == 0:
                return bytes([0x41, pid]) + _bitmap(car.all_pids(), pid)
            data = car.pid_bytes(pid)
            return None if data is None else bytes([0x41, pid]) + data
        if mode == 2:
            pid, frame = int(cmd[2:4], 16), int(cmd[4:6], 16)
            if frame != 0:
                return None
            if pid % 0x20 == 0:
                return bytes([0x42, pid, 0]) + _bitmap(car.ff_pids(), pid) if car.freeze else None
            data = car.ff_bytes(pid) if pid != 0x02 else (car.ff_bytes(0x02) or b"\x00\x00")
            return None if data is None else bytes([0x42, pid, 0]) + data
        if mode in (3, 7, 0x0A):
            if mode == 0x0A and not car.supports_0a:
                raise Neg(0x11)
            codes = {3: car.stored, 7: car.pending, 0x0A: car.permanent}[mode]
            return bytes([mode + 0x40, len(codes)]) + b"".join(encode_dtc(c) for c in codes)
        if mode == 4:
            if car.strict_clear and car.engine_on:
                raise Neg(0x22)
            car.clear()
            return bytes([0x44])
        if mode == 9:
            pid = int(cmd[2:4], 16)
            if pid == 0x00:
                return bytes([0x49, 0x00]) + _bitmap([2, 4], 0)
            if pid == 0x02:
                return bytes([0x49, 0x02, 0x01]) + car.VIN.encode()
            if pid == 0x04:
                return bytes([0x49, 0x04, 0x01]) + car.CAL_ID.encode().ljust(16, b"\x00")
            return None
        raise Neg(0x11)

    # ----------------------------------------------------------------- comandos
    def process(self, raw: str):
        """Receive one command line; return (response text | None, delay in seconds)."""
        self.log.append(raw)
        if self.drop_next > 0:
            self.drop_next -= 1
            return None, 0.0
        cmd = re.sub(r"\s+", "", raw).upper()
        if not cmd and self.last:
            cmd = self.last
        elif cmd:
            self.last = cmd
        echo = (raw.strip() + "\r") if self.echo else ""
        if self.garbage_next > 0:
            self.garbage_next -= 1
            return echo + "\x00\xff@@#\r\r>", 0.01
        lines, delay = self._dispatch(cmd)
        eol = self._eol()
        text = echo + "".join(l + eol for l in lines) + eol + ">"
        return text, delay * self.latency

    def _dispatch(self, cmd: str):
        if cmd.startswith("AT"):
            return self._at(cmd[2:])
        if not re.fullmatch(r"[0-9A-F]+", cmd) or len(cmd) < 2:
            return ["?"], 0.01
        return self._obd(cmd)

    def _at(self, a: str):
        if a == "Z":
            self.reset()
            return ["", self.version], 0.6
        if a == "WS":
            self.reset()
            return ["", self.version], 0.3
        if a == "I":
            return [self.version], 0.01
        if a == "@1":
            return ["OBD Solutions LLC"], 0.01
        m = re.fullmatch(r"([ELSH])([01])", a)
        if m:
            setattr(self, {"E": "echo", "L": "linefeed", "S": "spaces", "H": "headers"}[m.group(1)], m.group(2) == "1")
            return ["OK"], 0.01
        m = re.fullmatch(r"CAF([01])", a)
        if m:
            self.caf = m.group(1) == "1"
            return ["OK"], 0.01
        if re.fullmatch(r"AT[012]", a) or re.fullmatch(r"ST[0-9A-F]{1,2}", a) or a in ("D", "DEFAULTS") or re.fullmatch(r"M[01]", a):
            return ["OK"], 0.01
        m = re.fullmatch(r"SP([0-9A-C])", a)
        if m:
            self.proto = m.group(1)
            self.found = None if self.proto == "0" else self.proto
            return ["OK"], 0.01
        if a == "DPN":
            return [("A" if self.proto == "0" else "") + (self.found or self.proto)], 0.01
        if a == "DP":
            return ["AUTO, " + (self.found or "?")], 0.01
        if a == "RV":
            v = self.car.snap()["volt"] if self.car.ignition else 12.3
            return [f"{v:.1f}V"], 0.02
        return ["?"], 0.01

    def _obd(self, cmd: str):
        car = self.car
        mode = int(cmd[:2], 16)
        base = {1: 4, 2: 6, 3: 2, 4: 2, 7: 2, 9: 4, 0x0A: 2}.get(mode)
        if base is None:
            return ["?"], 0.01
        if len(cmd) == base + 1:
            if self.reject_count:
                return ["?"], 0.01
            if self.count_nodata:
                return ["NO DATA"], 0.2
            cmd = cmd[:-1]
        elif len(cmd) != base:
            return ["?"], 0.01
        lines: List[str] = []
        delay = 0.045
        if not car.ignition:
            return (["SEARCHING..."] if not self.found else []) + ["UNABLE TO CONNECT"], 1.5
        if not self.found:
            lines.append("SEARCHING...")
            delay += 1.2
            self.found = self.real_protocol
        try:
            payload = self._service(mode, cmd)
        except Neg as n:
            payload = bytes([0x7F, mode, n.nrc])
        if payload is None:
            return lines + ["NO DATA"], delay + 0.2
        return lines + self._render(payload), delay


class SimSerial:
    """pyserial.Serial-like object connected to the simulator (in memory)."""

    def __init__(self, sim: ELM327Sim, timeout: float = 0.1):
        self.sim = sim
        self.timeout = timeout
        self._in = bytearray()
        self._cmd = bytearray()
        self._cv = threading.Condition()
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._open = True
        self.sent: List[str] = []
        self._thread = threading.Thread(target=self._run, name="sim-elm327", daemon=True)
        self._thread.start()

    @property
    def is_open(self) -> bool:
        return self._open

    def _check(self):
        if not self._open:
            raise OSError("porta simulada fechada")

    def write(self, data: bytes) -> int:
        self._check()
        self._cmd += data
        while b"\r" in self._cmd:
            i = self._cmd.index(b"\r")
            line = self._cmd[:i].decode("ascii", "ignore")
            del self._cmd[:i + 1]
            self.sent.append(line)
            self._q.put(line)
        return len(data)

    @property
    def in_waiting(self) -> int:
        self._check()
        with self._cv:
            return len(self._in)

    def read(self, size: int = 1) -> bytes:
        self._check()
        end = time.monotonic() + self.timeout
        with self._cv:
            while not self._in:
                left = end - time.monotonic()
                if left <= 0 or not self._open:
                    return b""
                self._cv.wait(left)
            out = bytes(self._in[:size])
            del self._in[:size]
            return out

    def flush(self):
        pass

    def close(self):
        self._open = False
        self._q.put(None)
        with self._cv:
            self._cv.notify_all()

    def _run(self):
        while True:
            line = self._q.get()
            if line is None:
                return
            text, delay = self.sim.process(line)
            if text is None:
                continue
            if delay > 0:
                time.sleep(delay)
            with self._cv:
                self._in += text.encode("latin-1", "ignore")
                self._cv.notify_all()


def make_sim_serial(scenario: str = "check_engine", latency: float = 1.0, **kw) -> SimSerial:
    car_kw = {k: kw.pop(k) for k in ("supports_0a", "strict_clear", "seed", "clock") if k in kw}
    return SimSerial(ELM327Sim(SimCar(scenario, **car_kw), latency=latency, **kw))
