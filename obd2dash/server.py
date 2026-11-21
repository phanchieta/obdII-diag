"""Local web server (127.0.0.1 only): JSON API + live events (SSE) + the dashboard files."""
from __future__ import annotations

import concurrent.futures as cf
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import ROOT, __version__
from .i18n import BadRequest, Conflict, LANGS, LocalizedError, as_msg, msg
from .kb import KnowledgeBase
from .ports import bluetooth_diagnostics, list_ports
from .session import Session
from .simulator import SCENARIOS
from .store import make_report

WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.environ.get("OBD2DASH_DATA") or os.path.join(ROOT, "data")


class App:
    def __init__(self, data_dir: str = DATA_DIR, car_label: Optional[str] = None):
        if car_label is None:
            car_label = os.environ.get("OBD2DASH_CAR", "")
        self.kb = KnowledgeBase(car_label)
        self.session = Session(data_dir, self.kb)
        self.token = secrets.token_urlsafe(16)
        self.stopping = False


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OBD2Dash/" + __version__

        def log_message(self, fmt, *args):  # silent
            pass

        # ------------------------------------------------------------- helpers
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return re.fullmatch(r"(127\.0\.0\.1|localhost|\[::1\])(:\d+)?", host) is not None

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _err(self, code: int, m) -> None:
            self._json({"ok": False, "error": as_msg(m)}, code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                raise BadRequest(msg("http.body_big"))
            raw = self.rfile.read(n) if n else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                raise BadRequest(msg("http.bad_json"))
            return data if isinstance(data, dict) else {}

        # ----------------------------------------------------------------- GET
        def do_GET(self):
            if not self._host_ok():
                return self._err(403, msg("http.host"))
            url = urlparse(self.path)
            path = unquote(url.path)
            try:
                if path.startswith("/api/"):
                    return self._api_get(path, parse_qs(url.query))
                return self._static(path)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as e:  # noqa: BLE001
                return self._err(500, msg("http.internal", err=f"{type(e).__name__}: {e}"))

        def _static(self, path: str) -> None:
            if path in ("", "/"):
                path = "/index.html"
            rel = os.path.normpath(path.lstrip("/"))
            full = os.path.normpath(os.path.join(WEB_DIR, rel))
            if not full.startswith(WEB_DIR + os.sep) or not os.path.isfile(full):
                return self._err(404, msg("http.not_found"))
            with open(full, "rb") as f:
                data = f.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if full.endswith(".js"):
                ctype = "application/javascript"
            if full.endswith(".html"):
                data = data.replace(b"__TOKEN__", app.token.encode()).replace(b"__VERSION__", __version__.encode())
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self._send(200, data, ctype)

        def _api_get(self, path: str, q: dict) -> None:
            s = app.session
            if path == "/api/state":
                return self._json(s.snapshot())
            if path == "/api/stream":
                return self._stream()
            if path == "/api/ports":
                return self._json({"ok": True, "ports": list_ports(), "scenarios": SCENARIOS, "platform": sys.platform,
                                   "car": app.kb.car_label})
            if path == "/api/history":
                return self._json({"ok": True, "items": s.history.list()})
            m = re.fullmatch(r"/api/history/([0-9A-Za-z_-]+)", path)
            if m:
                doc = s.history.get(m.group(1))
                return self._json({"ok": True, "doc": doc}) if doc else self._err(404, msg("http.not_found"))
            m = re.fullmatch(r"/api/kb/([PCBUpcbu][0-9A-Fa-f]{4})", path)
            if m:
                return self._json({"ok": True, "kb": app.kb.lookup(m.group(1))})
            if path == "/api/report":
                lang = (q.get("lang") or ["en"])[0]
                text = make_report(s.snapshot(), app.kb.car_label, lang if lang in LANGS else "en")
                return self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            if path == "/api/raw":
                return self._json({"ok": True, "raw": s.raw_log(300)})
            if path == "/api/pids":
                from .pids import PIDS
                return self._json({"ok": True, "pids": {p.key: {"label": p.label, "unit": p.unit, "dec": p.dec,
                                                                 "interval": p.interval} for p in PIDS}})
            if path == "/api/diag/bluetooth":
                return self._json({"ok": True, **bluetooth_diagnostics()})
            return self._err(404, msg("http.no_route"))

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            since = None
            try:
                while not app.stopping:
                    snap = app.session.snapshot(since)
                    since = snap["seq"]
                    self.wfile.write(b"data: " + json.dumps(snap, ensure_ascii=False).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

        # ---------------------------------------------------------------- POST
        def _drain(self) -> None:
            """Read (a bounded amount of) the request body before an early error reply: closing a socket that still
            holds unread data makes Windows send a TCP reset, and the client then never sees our answer."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return
            if 0 < n <= 65536:
                self.rfile.read(n)

        def _post_rejection(self):
            if not self._host_ok():
                return msg("http.host")
            if self.headers.get("X-OBD-Token") != app.token:
                return msg("http.token")
            origin = self.headers.get("Origin")
            if origin and not re.fullmatch(r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?", origin):
                return msg("http.origin")
            return None

        def do_POST(self):
            rejected = self._post_rejection()
            if rejected is not None:
                self._drain()
                return self._err(403, rejected)
            path = urlparse(self.path).path
            s = app.session
            try:
                body = self._body()
                if path == "/api/connect":
                    s.connect(port=body.get("port") or "auto", baud=body.get("baud") or None, demo=bool(body.get("demo")),
                              scenario=body.get("scenario") or "check_engine", car=body.get("car"))
                    return self._json({"ok": True})
                if path == "/api/disconnect":
                    s.disconnect()
                    return self._json({"ok": True})
                if path == "/api/dtc/read":
                    return self._json({"ok": True, "result": s.read_dtcs_job()})
                if path == "/api/dtc/clear":
                    if body.get("confirm") is not True:
                        raise BadRequest(msg("http.confirm"))
                    return self._json({"ok": True, "result": s.clear_job()})
                if path == "/api/terminal":
                    return self._json({"ok": True, "result": s.terminal(str(body.get("cmd", "")))})
                return self._err(404, msg("http.no_route"))
            except Conflict as e:
                return self._err(409, e.msg)
            except BadRequest as e:
                return self._err(400, e.msg)
            except cf.TimeoutError:
                return self._err(504, msg("http.timeout"))
            except LocalizedError as e:
                return self._err(500, e.msg)
            except Exception as e:  # noqa: BLE001
                return self._err(500, msg("http.internal", err=f"{type(e).__name__}: {e}"))

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def find_port(start: int = 8765, tries: int = 20) -> int:
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            try:
                sk.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise OSError("No free local port between %d and %d." % (start, start + tries))


def create_server(app: App, port: int) -> Server:
    return Server(("127.0.0.1", port), make_handler(app))
