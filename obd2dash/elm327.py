"""ELM327 adapter driver.

Works with any pyserial-like object (read / write / in_waiting / close):
  * serial.Serial  -> COM port created by Windows for a paired Bluetooth adapter, /dev/ttyUSB0, /dev/rfcomm0 ...
  * RfcommSocket   -> direct Bluetooth RFCOMM socket (Linux)
  * SimSerial      -> simulator (demo mode and tests)
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, List, Optional

from . import protocol as P
from .i18n import LocalizedError, msg


class ELMError(LocalizedError):
    """Generic communication error with the adapter or the car."""


class ELMTimeout(ELMError):
    """The adapter did not return the '>' prompt in time."""


class ELMDisconnected(ELMError):
    """The serial / Bluetooth link dropped."""


class NoData(ELMError):
    """The car did not answer this request (NO DATA)."""


class UnableToConnect(ELMError):
    """The adapter could not talk to the ECU (ignition off?)."""


class BusError(ELMError):
    """Bus error (CAN ERROR, BUS ERROR, ...)."""


class BadCommand(ELMError):
    """The adapter did not recognise the command ('?')."""


class NegativeResponse(ELMError):
    def __init__(self, sid: int, nrc: int):
        super().__init__(msg("err.negative", sid=f"0x{sid:02X}", nrc=f"0x{nrc:02X}"))
        self.sid = sid
        self.nrc = nrc


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


class ELM327:
    def __init__(self, ser, log: Optional[Callable] = None, raw_log: Optional[Callable[[str, str], None]] = None,
                 timeout: float = 3.0):
        self.ser = ser
        self._log = log or (lambda m: None)
        self._raw = raw_log or (lambda d, t: None)
        self.timeout = timeout
        self.version = ""
        self.protocol: Optional[str] = None      # '6' = CAN 11 bit / 500 kbaud
        self.headers = True
        self.count_ok = True                     # adapter accepts the "expected number of responses" suffix
        self._count_fail = 0
        self._lock = threading.RLock()

    # ------------------------------------------------------------- low level
    def _write(self, data: bytes) -> None:
        try:
            self.ser.write(data)
        except Exception as e:  # SerialException, SerialTimeoutException, OSError...
            raise ELMDisconnected(msg("err.port_write", err=str(e))) from e

    def _drain(self) -> None:
        try:
            for _ in range(50):
                n = self.ser.in_waiting
                if not n:
                    break
                self.ser.read(n)
        except Exception as e:
            raise ELMDisconnected(msg("err.port_read", err=str(e))) from e

    def _read_until_prompt(self, timeout: float):
        buf = bytearray()
        end = time.monotonic() + timeout
        got = False
        while time.monotonic() < end:
            try:
                n = self.ser.in_waiting
                chunk = self.ser.read(n if n else 1)
            except Exception as e:
                raise ELMDisconnected(msg("err.port_read", err=str(e))) from e
            if chunk:
                buf += chunk
                if b">" in chunk:
                    got = True
                    break
        return buf.decode("ascii", "ignore").replace("\x00", ""), got

    @staticmethod
    def _clean(text: str, cmd: str) -> List[str]:
        parts = [p.strip() for p in re.split(r"[\r\n]+", text.replace(">", ""))]
        lines = [p for p in parts if p]
        if lines and _norm(lines[0]) == _norm(cmd):       # drop the echo of the command
            lines = lines[1:]
        return lines

    def _interrupt(self) -> None:
        """Abort a pending adapter operation (any character does; a space is harmless when idle)."""
        try:
            self._write(b" ")
            self._read_until_prompt(1.0)
        except ELMError:
            pass

    def command(self, cmd: str, timeout: Optional[float] = None) -> List[str]:
        """Send a command (AT... or hex OBD request); return the response lines without echo and prompt."""
        timeout = timeout or self.timeout
        with self._lock:
            self._drain()
            self._raw(">>", cmd)
            self._write((cmd + "\r").encode("ascii"))
            text, got = self._read_until_prompt(timeout)
            lines = self._clean(text, cmd)
            self._raw("<<", " | ".join(lines) + ("" if got else "   [NO PROMPT]"))
            if not got:
                self._interrupt()
                raise ELMTimeout(msg("err.timeout", cmd=cmd, secs=f"{timeout:.0f}"))
            return lines

    # ---------------------------------------------------------- initialisation
    def _cfg(self, cmd: str, optional: bool = False) -> bool:
        try:
            lines = self.command(cmd, 3.0)
        except ELMTimeout:
            if optional:
                return False
            raise
        ok = any(_norm(l) == "OK" for l in lines)
        if not ok and not optional:
            raise ELMError(msg("err.cmd_rejected", cmd=cmd, resp=" | ".join(lines) or "—"))
        return ok

    def initialize(self, protocol: str = "0", attempts: int = 3, atz_timeout: float = 6.0) -> None:
        """Reset the adapter and apply the configuration used by the app."""
        last: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                self._drain()
                lines = self.command("ATZ", atz_timeout)
                joined = " ".join(lines)
                m = re.search(r"ELM\s?327[^|]*", joined, re.I)
                self.version = (m.group(0).strip() if m else (joined or "ELM327"))
                break
            except ELMTimeout as e:
                last = e
                self._log(msg("log.no_atz", n=attempt, total=attempts))
                time.sleep(0.4)
        else:
            raise ELMError(msg("err.adapter_silent")) from last
        self._log(msg("log.adapter", version=self.version))
        self.headers = True
        self._cfg("ATE0")                       # echo off
        self._cfg("ATL0")                       # linefeeds off
        self._cfg("ATS1", optional=True)        # spaces on (easier to parse)
        self._cfg("ATH1")                       # headers on (shows which ECU answered)
        self._cfg("ATCAF1", optional=True)      # CAN auto-formatting
        self._cfg("ATAT1", optional=True)       # adaptive timing
        self._cfg("ATSP" + protocol)            # protocol: 0 = automatic
        self.protocol = None
        self.count_ok = True
        self._count_fail = 0

    # ---------------------------------------------------------------- info
    def read_protocol(self) -> Optional[str]:
        lines = self.command("ATDPN", 3.0)
        for l in lines:
            m = re.fullmatch(r"(A?)([0-9A-C])", _norm(l))
            if m:
                self.protocol = m.group(2) if m.group(2) != "0" else None
                return self.protocol
        return self.protocol

    def read_voltage(self) -> Optional[float]:
        lines = self.command("ATRV", 3.0)
        for l in lines:
            m = re.search(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*V?", l, re.I)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except ValueError:
                    pass
        return None

    # ----------------------------------------------------------------- OBD
    def _raise_for(self, parsed: P.Parsed, lines: List[str]) -> None:
        if parsed.messages:
            return
        st = set(parsed.status)
        if st & {"unable", "businit_error"}:
            raise UnableToConnect(msg("err.unable"))
        if "unknown_cmd" in st:
            raise BadCommand(msg("err.bad_command"))
        bus = st & {"canerror", "buserror", "busbusy", "bufferfull", "fberror", "dataerror", "lvreset", "err"}
        if bus:
            raise BusError(msg("err.bus", kinds=", ".join(sorted(bus))))
        if "stopped" in st:
            raise ELMError(msg("err.interrupted"))
        raise NoData(msg("err.no_data", resp=" | ".join(lines) or "—"))

    def query(self, req: str, timeout: Optional[float] = None, count: Optional[int] = None) -> P.Parsed:
        """Send an OBD request in hex (e.g. '010C') and return the parsed messages."""
        use_count = count is not None and self.count_ok
        cmd = req + (f"{count:X}" if use_count else "")
        lines = self.command(cmd, timeout)
        parsed = P.parse_response(lines, self.protocol, self.headers)
        if use_count and not parsed.messages:
            if "unknown_cmd" in parsed.status:
                self.count_ok = False
                self._log(msg("log.count_unsupported"))
                return self.query(req, timeout, None)
            if "nodata" in parsed.status:
                # some clones answer NO DATA to the suffix: confirm without it before giving up
                retry = self.query(req, timeout, None)      # a second NO DATA raises as usual
                self._count_fail += 1
                if self._count_fail >= 2:
                    self.count_ok = False
                    self._log(msg("log.count_disabled"))
                return retry
        self._raise_for(parsed, lines)
        return parsed

    def probe_vehicle(self, timeout: float = 35.0) -> P.Parsed:
        """First request (0100): makes the adapter search for the car's protocol."""
        lines = self.command("0100", timeout)
        try:
            self.read_protocol()
        except ELMError:
            pass
        parsed = P.parse_response(lines, self.protocol, self.headers)
        self._raise_for(parsed, lines)
        return parsed

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
