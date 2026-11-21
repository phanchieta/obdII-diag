"""Reading history (JSON files in data/history) and the plain-text report."""
from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional

from .i18n import FALLBACK, LANGS, pick, tr
from .pids import PID_BY_KEY

EVENTS = ("reading", "before_clear", "after_clear")


class History:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def save(self, event: str, payload: dict, demo: bool = False) -> str:
        base = time.strftime("%Y%m%d-%H%M%S") + "-" + event
        hid, n = base, 1
        while os.path.exists(os.path.join(self.folder, hid + ".json")):
            n += 1
            hid = f"{base}-{n}"
        doc = {"id": hid, "ts": time.time(), "ts_text": time.strftime("%Y-%m-%d %H:%M:%S"),
               "event": event, "demo": bool(demo), "data": payload}
        with open(os.path.join(self.folder, hid + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        return hid

    def list(self, limit: int = 200) -> List[dict]:
        out = []
        try:
            names = sorted((n for n in os.listdir(self.folder) if n.endswith(".json")), reverse=True)[:limit]
        except OSError:
            return out
        for n in names:
            try:
                with open(os.path.join(self.folder, n), encoding="utf-8") as f:
                    d = json.load(f)
                data = d.get("data", {})
                out.append({
                    "id": d["id"], "ts": d.get("ts"), "ts_text": d.get("ts_text", ""), "event": d.get("event", ""),
                    "demo": d.get("demo", False), "mil": (data.get("mil") or {}).get("on"),
                    "stored": [c["code"] for c in data.get("stored", [])],
                    "pending": [c["code"] for c in data.get("pending", [])],
                })
            except (OSError, ValueError, KeyError):
                continue
        return out

    def get(self, hid: str) -> Optional[dict]:
        if not re.fullmatch(r"[0-9A-Za-z_-]+", hid or ""):
            return None
        try:
            with open(os.path.join(self.folder, hid + ".json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


def _num(v, dec: int, lang: str) -> str:
    if isinstance(v, float):
        s = f"{v:.{dec}f}"
        return s.replace(".", ",") if lang == "pt" else s
    return str(v)


def make_report(state: dict, car_label: str = "", lang: str = FALLBACK) -> str:
    """Plain text to paste into a chat / send to a mechanic."""
    lang = lang if lang in LANGS else FALLBACK
    T = lambda key, **kw: tr(key, lang, **kw)  # noqa: E731
    L: List[str] = []
    ad, ve = state.get("adapter", {}), state.get("vehicle", {})
    L.append(T("rep.title", car=car_label or "OBD-II"))
    L.append(T("rep.generated", date=time.strftime("%Y-%m-%d %H:%M")))
    volt = (_num(ad["voltage"], 1, lang) + " V") if ad.get("voltage") else "—"
    L.append(T("rep.adapter", adapter=ad.get("version") or "—", protocol=ve.get("protocol") or "—", volt=volt))
    L.append(T("rep.vehicle", vin=ve.get("vin") or "—", cal=ve.get("cal_id") or "—", std=pick(ve.get("obd_standard"), lang) or "—"))
    d = state.get("dtc")
    if not d:
        L.append("\n" + T("rep.no_reading"))
        return "\n".join(L + ["", T("rep.footer")])
    mil = d.get("mil")
    mil_text = T("rep.mil_on") if mil and mil["on"] else T("rep.mil_off") if mil else T("rep.mil_unknown")
    L.append(T("rep.mil", state=mil_text) + (T("rep.mil_count", n=mil["count"]) if mil else ""))

    def block(title_key, items):
        L.append("\n" + T(title_key))
        if items is None:
            L.append("  " + T("rep.unsupported"))
        elif not items:
            L.append("  " + T("rep.none"))
        for c in items or []:
            k = c["kb"]["t"][lang]
            L.append("  - " + T("rep.code_line", code=c["code"], title=k["title"], sev=k["severity_text"], system=k["system"]))

    block("rep.stored", d.get("stored"))
    block("rep.pending", d.get("pending"))
    block("rep.permanent", d.get("permanent") if d.get("permanent_supported") else None)
    ff = d.get("freeze")
    if ff:
        L.append("\n" + T("rep.freeze", code=ff["dtc"]))
        for v in ff["values"]:
            L.append(f"  - {pick(v['label'], lang)}: {_num(v['value'], v.get('dec', 0), lang)} {v['unit']}".rstrip())
    mons = d.get("monitors") or []
    if mons:
        L.append("\n" + T("rep.monitors"))
        for m in mons:
            L.append(f"  - {pick(m['name'], lang)}: {T('rep.ready') if m['ready'] else T('rep.not_ready')}")
    live = state.get("live") or {}
    shown = []
    for key, item in live.items():
        p = PID_BY_KEY.get(key)
        v = item.get("v")
        if p and v is not None:
            shown.append(f"  - {pick(p.label, lang)}: " + (pick(v, lang) if isinstance(v, dict) else f"{_num(v, p.dec, lang)} {p.unit}".rstrip()))
    if shown:
        L.append("\n" + T("rep.live"))
        L.extend(shown)
    for tip in d.get("tips") or []:
        L.append("\n" + T("rep.tip", text=pick(tip, lang)))
    for w in d.get("warnings") or []:
        L.append(T("rep.warning", text=pick(w, lang)))
    return "\n".join(L + ["", T("rep.footer")])
