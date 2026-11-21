"""Serial / Bluetooth port discovery for Windows and Linux (macOS: best effort).

Windows : each paired Bluetooth SPP adapter gets a virtual COM port; friendly names come from PowerShell.
Linux   : /dev/ttyUSB*, /dev/rfcomm* through pyserial; paired Bluetooth adapters (bluetoothctl) can also be used
          directly through an RFCOMM socket (``bt://AA:BB:CC:DD:EE:FF``), no rfcomm / root needed.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List

from .transport import bluetooth_sockets_available

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

_CACHE: Dict[str, object] = {"t": float("-inf"), "names": {}}      # t: time.monotonic() of the last lookup
_OBD_LIKE = re.compile(r"OBD|ELM|V-?LINK|VGATE|VEEPEAK|KONNWEI|SCAN|CAR", re.I)
_MAC = re.compile(r"((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})")


def _run(cmd: List[str], timeout: float = 8.0) -> str:
    kw = {"creationflags": 0x08000000} if IS_WINDOWS else {}          # no console window on Windows
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


# ------------------------------------------------------------------ Windows

_PS_HEAD = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ErrorActionPreference='SilentlyContinue'; "

_PS_NAMES = _PS_HEAD + r"""
Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -like 'BTHENUM\DEV_*' } | ForEach-Object {
  [pscustomobject]@{ mac = ($_.InstanceId -replace '^BTHENUM\\DEV_([0-9A-Fa-f]{12}).*$','$1').ToUpper(); name = $_.FriendlyName }
} | ConvertTo-Json -Compress
"""

# Radios, paired devices, COM ports and a nearby-device scan (this is what shows whether the adapter is visible at all).
_PS_DIAG = _PS_HEAD + r"""
$radios = @(Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -match '^(USB|PCI|ACPI)\\' } | ForEach-Object {
  $p = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_ProblemCode').Data
  '{0} | status={1} | problem code={2}' -f $_.FriendlyName, $_.Status, $p })
$paired = @(Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -like 'BTHENUM\DEV_*' } | ForEach-Object { '{0} | status={1}' -f $_.FriendlyName, $_.Status })
$com = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\(COM\d+\)' } | ForEach-Object { $_.Name })
$state = ''; $nearby = @()
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($op, $type, $secs = 30) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); if ($t.Wait($secs * 1000)) { $t.Result } else { $null } }
  $null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Radios.RadioAccessStatus, Windows.System.Devices, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Enumeration.DeviceInformationCollection, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Bluetooth.BluetoothDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
  $null = [Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
  $null = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
  $rs = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
  $state = (@($rs | Where-Object { $_.Kind -eq 'Bluetooth' } | ForEach-Object { '{0}: {1}' -f $_.Name, $_.State }) -join '; ')
  foreach ($kind in @('classic', 'LE')) {
    if ($kind -eq 'classic') { $sel = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelectorFromPairingState($false) } else { $sel = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromPairingState($false) }
    $col = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($sel)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
    foreach ($d in $col) { if ($d.Name) { $nearby += ('{0} | {1} | {2}' -f $d.Name, $kind, ($d.Id -replace '^.*-', '')) } }
  }
} catch { $state = "error: $_" }
[pscustomobject]@{ radios = $radios; paired = $paired; com = $com; radio_state = $state; nearby = @($nearby) } | ConvertTo-Json -Compress -Depth 4
"""


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def bluetooth_names(max_age: float = 20.0) -> Dict[str, str]:
    """MAC (12 hex digits, upper case) -> name of the paired Bluetooth device (Windows)."""
    if not IS_WINDOWS:
        return {}
    if time.monotonic() - float(_CACHE["t"]) < max_age:
        return _CACHE["names"]  # type: ignore[return-value]
    names: Dict[str, str] = {}
    txt = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS_NAMES], 15.0).strip()
    if txt:
        try:
            for d in _as_list(json.loads(txt)):
                if d.get("mac") and d.get("name"):
                    names[d["mac"].upper()] = d["name"]
        except (ValueError, AttributeError):
            pass
    _CACHE["t"], _CACHE["names"] = time.monotonic(), names
    return names


# -------------------------------------------------------------------- Linux

def linux_paired_bluetooth() -> List[dict]:
    """Paired Bluetooth devices known to BlueZ: [{'mac', 'name'}]."""
    out: List[dict] = []
    for cmd in (["bluetoothctl", "devices", "Paired"], ["bluetoothctl", "paired-devices"]):
        for line in _run(cmd, 6.0).splitlines():
            m = re.match(r"^Device\s+" + _MAC.pattern + r"\s*(.*)$", line.strip())
            if m:
                out.append({"mac": m.group(1).upper(), "name": m.group(2).strip()})
        if out:
            break
    return out


# ------------------------------------------------------------------- common

def _natural(dev: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", dev)]


def list_ports(with_names: bool = True) -> List[dict]:
    """All candidate ports. Every item: device, description, hwid, bluetooth, incoming, mac, bt_name, kind."""
    from serial.tools import list_ports as lp

    names = bluetooth_names() if (with_names and IS_WINDOWS) else {}
    out: List[dict] = []
    for p in sorted(lp.comports(), key=lambda p: _natural(p.device)):
        hwid = (p.hwid or "").upper()
        desc = p.description or ""
        bt = "BTHENUM" in hwid or "BLUETOOTH" in desc.upper() or "rfcomm" in p.device.lower()
        m = re.search(r"&([0-9A-F]{12})_", hwid)
        mac = m.group(1) if m else None
        zeros = bool(mac) and set(mac or "") == {"0"}          # MAC 000000000000 = incoming port (Windows)
        incoming = bt and (zeros or (mac is None and "LOCALMFG" in hwid))
        kind = "bluetooth" if bt else "usb" if hwid.startswith("USB") else "serial"
        out.append({"device": p.device, "description": desc, "hwid": p.hwid, "bluetooth": bt, "incoming": incoming,
                    "mac": mac, "bt_name": names.get(mac) if mac else None, "kind": kind})
    if IS_LINUX and bluetooth_sockets_available() and with_names:
        for d in linux_paired_bluetooth():
            out.append({"device": "bt://" + d["mac"], "description": "Bluetooth RFCOMM", "hwid": d["mac"], "bluetooth": True,
                        "incoming": False, "mac": d["mac"], "bt_name": d["name"], "kind": "bluetooth-socket"})
    return out


def auto_candidates(ports: List[dict]) -> List[str]:
    """Bluetooth outgoing ports first (adapter-like names first), then USB serial adapters."""
    bt = [p for p in ports if p["bluetooth"] and not p["incoming"]] or [p for p in ports if p["bluetooth"]]
    bt.sort(key=lambda p: (0 if _OBD_LIKE.search(p.get("bt_name") or "") else 1, _natural(p["device"])))
    usb = [p for p in ports if p.get("kind") == "usb"]
    return [p["device"] for p in bt] + [p["device"] for p in usb]


def bluetooth_diagnostics() -> dict:
    """Read-only look at the system's Bluetooth: {'platform', 'sections': [{'key', 'lines': [...]}]}."""
    sections: List[dict] = []
    if IS_WINDOWS:
        txt = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS_DIAG], 120.0).strip()
        try:
            d = json.loads(txt)
        except ValueError:
            return {"platform": "windows", "sections": [], "error": True}
        sections = [
            {"key": "radios", "lines": [str(x) for x in _as_list(d.get("radios"))]},
            {"key": "radio_state", "lines": [d.get("radio_state") or ""]},
            {"key": "paired", "lines": [str(x) for x in _as_list(d.get("paired"))]},
            {"key": "com", "lines": [str(x) for x in _as_list(d.get("com"))]},
            {"key": "nearby", "lines": [str(x) for x in _as_list(d.get("nearby"))]},
        ]
        return {"platform": "windows", "sections": sections}
    if IS_LINUX:
        sections.append({"key": "controller", "lines": [l.strip() for l in _run(["bluetoothctl", "show"], 6.0).splitlines()
                                                         if re.search(r"Controller|Powered|Discoverable|Pairable", l)]})
        sections.append({"key": "rfkill", "lines": [l for l in _run(["rfkill", "list", "bluetooth"], 5.0).splitlines() if l.strip()]})
        sections.append({"key": "paired", "lines": [f"{d['name']} | {d['mac']}" for d in linux_paired_bluetooth()]})
        scan = _run(["bluetoothctl", "--timeout", "8", "scan", "on"], 20.0)
        found = sorted({f"{m.group(2).strip() or '(no name)'} | {m.group(1).upper()}"
                        for m in (re.search(r"Device\s+" + _MAC.pattern + r"\s*(.*)$", l) for l in scan.splitlines()) if m})
        sections.append({"key": "nearby", "lines": found})
        ports = [p for p in (_dev for _dev in _safe_ports()) if not p.startswith("/dev/ttyS")]
        sections.append({"key": "serial", "lines": ports})
        try:
            import grp
            groups = sorted(g.gr_name for g in grp.getgrall() if os.environ.get("USER", "") in g.gr_mem)
            groups.append("(primary: " + grp.getgrgid(os.getgid()).gr_name + ")")
        except Exception:
            groups = []
        sections.append({"key": "groups", "lines": [", ".join(groups)]})
        sections.append({"key": "sockets", "lines": ["yes" if bluetooth_sockets_available() else "no"]})
        return {"platform": "linux", "sections": sections}
    return {"platform": sys.platform, "sections": [{"key": "serial", "lines": _safe_ports()}]}


def _safe_ports() -> List[str]:
    try:
        return [p["device"] for p in list_ports(with_names=False)]
    except Exception:
        return []
