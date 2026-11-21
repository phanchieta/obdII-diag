"""OBD-II services (SAE J1979) on top of the ELM327: PIDs, trouble codes, clear codes, VIN..."""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Set

from . import pids as K
from . import protocol as P
from .elm327 import ELM327, ELMError, NegativeResponse, NoData
from .i18n import Msg, msg


class OBD:
    def __init__(self, elm: ELM327):
        self.elm = elm
        self.supported: Set[int] = set()
        self.supported_ff: Set[int] = set()

    @property
    def can(self) -> bool:
        return P.is_can(self.elm.protocol)

    # ---------------------------------------------------------------- helpers
    def _find(self, parsed: P.Parsed, mode: int, pid: Optional[int] = None) -> List[P.Message]:
        want = mode + 0x40
        good = [m for m in parsed.messages
                if m.data and m.data[0] == want and (pid is None or (len(m.data) > 1 and m.data[1] == pid))]
        if good:
            good.sort(key=lambda m: 0 if m.ecu in ("7E8", "18DAF110", "11", "") else 1)
            return good
        for m in parsed.messages:
            if len(m.data) >= 3 and m.data[0] == 0x7F and m.data[1] == mode:
                raise NegativeResponse(mode, m.data[2])
        raise NoData(msg("err.unexpected_response", resp=" | ".join(m.data.hex(" ") for m in parsed.messages)))

    def _query(self, req: str, timeout: Optional[float] = None, count: Optional[int] = None) -> P.Parsed:
        """Query handling 'response pending' (NRC 0x78)."""
        for _ in range(4):
            parsed = self.elm.query(req, timeout, count)
            pending = [m for m in parsed.messages if len(m.data) >= 3 and m.data[0] == 0x7F and m.data[2] == 0x78]
            others = [m for m in parsed.messages if m not in pending]
            if pending and not others:
                time.sleep(0.4)
                continue
            return parsed
        return parsed

    # ------------------------------------------------------------------ PIDs
    def read_pid_raw(self, pid: int, mode: int = 0x01, frame: int = 0) -> bytes:
        req = f"{mode:02X}{pid:02X}" + (f"{frame:02X}" if mode == 0x02 else "")
        parsed = self._query(req, count=1)
        m = self._find(parsed, mode, pid)[0]
        return bytes(m.data[3 if mode == 0x02 else 2:])

    def read_pid(self, pid: int, mode: int = 0x01):
        return K.decode_pid(pid, self.read_pid_raw(pid, mode))

    def supported_pids(self, mode: int = 0x01) -> Set[int]:
        found: Set[int] = set()
        base = 0x00
        while base <= 0xC0:
            try:
                raw = self.read_pid_raw(base, mode)
            except (NoData, NegativeResponse):
                break
            if len(raw) < 4:
                break
            bitmap = int.from_bytes(raw[:4], "big")
            for i in range(32):
                if bitmap & (1 << (31 - i)):
                    found.add(base + i + 1)
            if (base + 0x20) not in found:
                break
            base += 0x20
        if mode == 0x01:
            self.supported = found
        else:
            self.supported_ff = found
        return found

    # --------------------------------------------------- MIL / monitors
    def read_mil_status(self) -> dict:
        raw = self.read_pid_raw(0x01)
        if len(raw) < 4:
            raise NoData(msg("err.short_pid", pid="01"))
        return K.decode_monitor_status(*raw[:4])

    def read_obd_standard(self) -> Optional[Msg]:
        try:
            raw = self.read_pid_raw(0x1C)
        except (NoData, NegativeResponse):
            return None
        if not raw:
            return None
        return K.OBD_STANDARDS.get(raw[0]) or Msg(pt=f"Código {raw[0]}", en=f"Code {raw[0]}")

    # -------------------------------------------------------------- DTCs
    def read_dtcs(self, mode: int = 0x03) -> List[dict]:
        """03 = stored, 07 = pending, 0A = permanent. Returns [{'code', 'ecu'}]."""
        try:
            parsed = self._query(f"{mode:02X}", timeout=6.0)
        except NoData:
            return []
        out, seen = [], set()
        for m in self._find(parsed, mode):
            for code in K.decode_dtc_payload(m.data, self.can):
                if (m.ecu, code) not in seen:
                    seen.add((m.ecu, code))
                    out.append({"code": code, "ecu": m.ecu})
        return out

    def clear_dtcs(self) -> bool:
        parsed = self._query("04", timeout=10.0)
        self._find(parsed, 0x04)        # raises NegativeResponse if the ECU refuses
        return True

    # ------------------------------------------------------ freeze frame
    def read_freeze_frame(self) -> Optional[dict]:
        """Data frozen when the first code was stored (mode 02, frame 0)."""
        trigger = None
        try:
            raw = self.read_pid_raw(0x02, mode=0x02)
            if len(raw) >= 2:
                trigger = K.decode_dtc(raw[0], raw[1])
        except (NoData, NegativeResponse):
            return None
        if not trigger:
            return None
        if not self.supported_ff:
            try:
                self.supported_pids(mode=0x02)
            except ELMError:
                pass
        wanted = [p for p in K.FREEZE_PIDS if (not self.supported_ff) or p in self.supported_ff]
        values = []
        for pid in wanted:
            p = K.PID_BY_NUM[pid]
            try:
                v = K.decode_pid(pid, self.read_pid_raw(pid, mode=0x02))
            except (NoData, NegativeResponse, ValueError):
                continue
            values.append({"key": p.key, "label": p.label, "unit": p.unit, "value": v, "dec": p.dec})
        return {"dtc": trigger, "values": values}

    # ---------------------------------------------------- identification
    def read_vin(self) -> Optional[str]:
        try:
            parsed = self._query("0902", timeout=10.0)
            msgs = self._find(parsed, 0x09, 0x02)
        except (NoData, NegativeResponse):
            return None
        by_ecu: Dict[str, str] = {}
        for m in msgs:
            by_ecu[m.ecu] = by_ecu.get(m.ecu, "") + "".join(chr(b) for b in m.data[3:] if chr(b).isalnum() and b < 128)
        for s in by_ecu.values():
            if len(s) >= 17:
                return s[-17:]
        return max(by_ecu.values(), key=len, default=None) or None

    def read_calibration_id(self) -> Optional[str]:
        try:
            parsed = self._query("0904", timeout=10.0)
            msgs = self._find(parsed, 0x09, 0x04)
        except (NoData, NegativeResponse):
            return None
        s = "".join(chr(b) for b in msgs[0].data[3:] if 32 <= b < 127).strip()
        return s or None
