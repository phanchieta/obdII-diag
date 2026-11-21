"""Parse the text returned by the ELM327 into binary OBD messages.

Pure functions (no I/O), so they can be tested without a car.

Handled formats:
  * CAN (ISO 15765-4) with headers on (ATH1): "7E8 06 41 00 BE 3F A8 13"
    - single frames (SF) and multi-frame (FF + CF) messages are reassembled (ISO-TP)
  * CAN with headers off (ATH0): "41 0C 1A F8" and the format
    "014" / "0: 49 02 01 ..." / "1: ..." for long answers
  * J1850 / ISO 9141-2 / KWP2000: "48 6B 11 41 0C 1A F8 3A" (header + data + checksum)
  * Status lines: SEARCHING..., NO DATA, UNABLE TO CONNECT, CAN ERROR, ...
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CAN_11 = {"6", "8", "B", "C"}
CAN_29 = {"7", "9"}
CAN_ALL = CAN_11 | CAN_29

PROTOCOL_NAMES = {
    "0": "Auto",
    "1": "SAE J1850 PWM (41,6 kbaud)",
    "2": "SAE J1850 VPW (10,4 kbaud)",
    "3": "ISO 9141-2 (5 baud init)",
    "4": "ISO 14230-4 KWP (5 baud init)",
    "5": "ISO 14230-4 KWP (fast init)",
    "6": "ISO 15765-4 CAN (11 bit, 500 kbaud)",
    "7": "ISO 15765-4 CAN (29 bit, 500 kbaud)",
    "8": "ISO 15765-4 CAN (11 bit, 250 kbaud)",
    "9": "ISO 15765-4 CAN (29 bit, 250 kbaud)",
    "A": "SAE J1939 CAN (29 bit, 250 kbaud)",
    "B": "USER1 CAN (11 bit, 125 kbaud)",
    "C": "USER2 CAN (11 bit, 50 kbaud)",
}


def protocol_name(num) -> str:
    return PROTOCOL_NAMES.get(str(num).upper(), f"Protocolo {num}")


def is_can(num) -> bool:
    return str(num).upper() in CAN_ALL


_HEXLINE = re.compile(r"^[0-9A-F: ]+$")


def status_kind(line: str) -> Optional[str]:
    """Classify lines that are NOT data. Returns None if it looks like a data line."""
    s = line.strip().upper()
    if not s or _HEXLINE.match(s):
        return None
    if s == "?":
        return "unknown_cmd"
    if s == "OK":
        return "ok"
    if s.startswith("SEARCHING"):
        return "searching"
    if "UNABLE TO CONNECT" in s:
        return "unable"
    if s.startswith("BUS INIT"):
        return "businit_error" if "ERROR" in s else "businit"
    if "NO DATA" in s:
        return "nodata"
    if "CAN ERROR" in s:
        return "canerror"
    if "BUS ERROR" in s:
        return "buserror"
    if "BUS BUSY" in s:
        return "busbusy"
    if "BUFFER FULL" in s:
        return "bufferfull"
    if "DATA ERROR" in s or "RX ERROR" in s:
        return "dataerror"
    if "FB ERROR" in s:
        return "fberror"
    if "LV RESET" in s:
        return "lvreset"
    if "STOPPED" in s:
        return "stopped"
    if "ACT ALERT" in s or "LP ALERT" in s:
        return "alert"
    if s.startswith("ERR"):
        return "err"
    return "other"


@dataclass
class Message:
    ecu: str            # '7E8' (CAN) or source address '11' (K-line); '' if headers are off
    data: bytes         # OBD payload: [mode+0x40, pid, ...]
    complete: bool = True


@dataclass
class Parsed:
    messages: List[Message] = field(default_factory=list)
    status: List[str] = field(default_factory=list)


def _hexbytes(tokens) -> bytes:
    return bytes(int(t, 16) for t in tokens)


def _tokens(s: str, proto: Optional[str]) -> List[str]:
    """Split a line into tokens. Accepts lines with or without spaces."""
    if " " in s:
        return s.split()
    if proto in CAN_29:
        head, rest = s[:8], s[8:]
        return [head[i:i + 2] for i in range(0, len(head), 2)] + [rest[i:i + 2] for i in range(0, len(rest), 2)]
    if proto in CAN_11:
        head, rest = s[:3], s[3:]
        return [head] + [rest[i:i + 2] for i in range(0, len(rest), 2)]
    return [s[i:i + 2] for i in range(0, len(s), 2)]


def isotp_assemble(frames: List[bytes]) -> List[Tuple[bytes, bool]]:
    """Reassemble ISO-TP frames from ONE ECU. Returns a list of (payload, complete)."""
    out: List[Tuple[bytes, bool]] = []
    cur: Optional[bytearray] = None
    expected = 0
    for f in frames:
        if not f:
            continue
        pci = f[0] >> 4
        if pci == 0:                                   # single frame
            n = f[0] & 0x0F
            out.append((bytes(f[1:1 + n]), True))
        elif pci == 1 and len(f) >= 2:                 # first frame
            if cur is not None:
                out.append((bytes(cur), False))
            expected = ((f[0] & 0x0F) << 8) | f[1]
            cur = bytearray(f[2:])
        elif pci == 2:                                 # consecutive frame
            if cur is None:
                continue
            cur += f[1:]
            if len(cur) >= expected:
                out.append((bytes(cur[:expected]), True))
                cur = None
        elif pci == 3:                                 # flow control: ignore
            continue
        else:
            # first byte >= 0x40 (e.g. 0x41, 0x43, 0x7F): the adapter already removed the PCI byte
            out.append((bytes(f), True))
    if cur is not None:
        out.append((bytes(cur), False))
    return out


def parse_response(lines: List[str], proto: Optional[str] = None, headers: bool = True) -> Parsed:
    """Convert the text lines of a response into OBD messages."""
    proto = None if proto is None else str(proto).upper()
    out = Parsed()
    frames: Dict[str, List[bytes]] = {}
    order: List[str] = []
    groups: List[bytearray] = []        # "0:", "1:" format (ATH0, multi-frame)

    for raw in lines:
        s = raw.strip().upper().lstrip("<").strip()
        if not s:
            continue
        kind = status_kind(s)
        if kind is not None:
            out.status.append(kind)
            continue
        m = re.match(r"^([0-9A-F]):\s*(.*)$", s)
        if m:
            idx = int(m.group(1), 16)
            try:
                chunk = _hexbytes(m.group(2).split())
            except ValueError:
                out.status.append("garbage")
                continue
            if idx == 0 or not groups:
                groups.append(bytearray())
            groups[-1] += chunk
            continue
        if " " not in s and len(s) == 3:                # byte-count line ("014")
            continue
        toks = _tokens(s, proto)
        try:
            if not headers:
                out.messages.append(Message("", _hexbytes(toks)))
            elif proto in CAN_ALL or (proto is None and len(toks[0]) == 3):
                if proto in CAN_29:
                    ecu, data = "".join(toks[:4]), _hexbytes(toks[4:])
                else:
                    ecu, data = toks[0], _hexbytes(toks[1:])
                if ecu not in frames:
                    frames[ecu] = []
                    order.append(ecu)
                frames[ecu].append(data)
            else:
                b = _hexbytes(toks)
                hdr = 4 if (proto in ("4", "5") and b and (b[0] & 0x3F) == 0) else 3
                if len(b) <= hdr + 1:
                    continue
                out.messages.append(Message("%02X" % b[2], b[hdr:-1]))
        except ValueError:
            out.status.append("garbage")

    for ecu in order:
        for payload, complete in isotp_assemble(frames[ecu]):
            if payload:
                out.messages.append(Message(ecu, payload, complete))
    for g in groups:
        if g:
            out.messages.append(Message("", bytes(g)))
    return out
