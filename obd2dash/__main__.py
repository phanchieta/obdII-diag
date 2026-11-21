"""Start the dashboard:  python -m obd2dash [--demo] [--auto] [--com COM5] [--car "Honda Fit 2011"] [--port 8765]"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser

from . import __version__
from .server import App, create_server, find_port
from .simulator import SCENARIOS


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="OBD-II dashboard for ELM327 adapters (Bluetooth / USB)")
    ap.add_argument("--port", type=int, default=8765, help="local HTTP port (default 8765)")
    ap.add_argument("--demo", action="store_true", help="use the built-in car simulator (no car needed)")
    ap.add_argument("--scenario", default="check_engine", choices=sorted(SCENARIOS), help="simulator scenario")
    ap.add_argument("--auto", action="store_true", help="connect to the adapter automatically on start")
    ap.add_argument("--com", help="serial port of the adapter (COM5, /dev/ttyUSB0, bt://AA:BB:CC:DD:EE:FF); implies --auto")
    ap.add_argument("--car", default=None, help='vehicle name, e.g. "Honda Fit 2011" (enables brand tips; env OBD2DASH_CAR)')
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    app = App(car_label=args.car)
    port = find_port(args.port)
    server = create_server(app, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"OBD-II Diag v{__version__}", flush=True)
    print(f"Open in your browser / Abra no navegador: {url}", flush=True)
    print("Press Ctrl+C to stop / Ctrl+C para encerrar.\n", flush=True)

    if args.demo:
        app.session.connect(demo=True, scenario=args.scenario)
    elif args.auto or args.com:
        app.session.connect(port=args.com or "auto")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        app.stopping = True
        app.session.disconnect()
        server.server_close()
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
