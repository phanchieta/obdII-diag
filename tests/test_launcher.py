"""The launchers: run.py and the run.bat / run.sh wrappers start the dashboard, and the demo works end to end
in a real subprocess (this is what a friend does right after cloning the repository)."""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import obd2dash  # noqa: F401
from obd2dash import ROOT

sys.path.insert(0, ROOT)
import run as run_module  # noqa: E402  (the launcher script at the project root)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90,
                          universal_newlines=True, **kw)


def _bytes(*parts):
    with open(os.path.join(ROOT, *parts), "rb") as f:
        return f.read()


WINDOWS_SCRIPTS = (("run.bat",), ("demo.bat",), ("scripts", "get-python-windows.ps1"))
UNIX_SCRIPTS = (("run.sh",), ("demo.sh",), ("run.py",))


class LauncherArgsTests(unittest.TestCase):
    def test_connects_automatically_by_default(self):
        self.assertEqual(run_module._args([]), ["--auto"])
        self.assertEqual(run_module._args(["--port", "9000"]), ["--auto", "--port", "9000"])
        self.assertEqual(run_module._args(["--car", "Honda Fit 2011"]), ["--auto", "--car", "Honda Fit 2011"])

    def test_explicit_choices_are_respected(self):
        for argv in (["--demo"], ["--auto"], ["--com", "COM5"], ["--com=/dev/ttyUSB0"], ["--help"], ["-h"]):
            self.assertEqual(run_module._args(argv), argv)

    def test_old_python_gets_a_friendly_message(self):
        code = ("import sys; sys.version_info = (3, 6, 9); sys.argv = ['run.py']; "
                "exec(open('run.py', encoding='utf-8').read())")
        r = _run([sys.executable, "-c", code])
        self.assertEqual(r.returncode, 1)
        self.assertIn("3.8", r.stdout)
        self.assertIn("Python 3.6", r.stdout)
        self.assertIn("mais novo", r.stdout)            # the Portuguese line is there too


class LauncherScriptTests(unittest.TestCase):
    def test_run_py_help(self):
        r = _run([sys.executable, "run.py", "--help"])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("--demo", r.stdout)

    def test_wrapper_script_finds_python_and_starts(self):
        if os.name == "nt":
            cmd = [os.path.join(ROOT, "run.bat"), "--help"]
        else:
            cmd = ["sh", os.path.join(ROOT, "run.sh"), "--help"]
        r = _run(cmd)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("--demo", r.stdout)

    def test_demo_wrapper_passes_demo_flag(self):
        # demo.sh / demo.bat only add --demo; look at the scripts instead of starting a second server
        self.assertIn(b"--demo", _bytes("demo.sh"))
        self.assertIn(b"--demo", _bytes("demo.bat"))

    def test_scripts_have_the_right_line_endings(self):
        """cmd.exe misreads labels in LF-only batch files, and /bin/sh chokes on CRLF (.gitattributes enforces both)."""
        for parts in WINDOWS_SCRIPTS:
            raw = _bytes(*parts)
            self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), "/".join(parts) + " must use CRLF")
        for parts in UNIX_SCRIPTS:
            self.assertNotIn(b"\r", _bytes(*parts), "/".join(parts) + " must use LF")

    def test_windows_scripts_are_ascii(self):
        for parts in WINDOWS_SCRIPTS:
            self.assertTrue(all(b < 128 for b in bytearray(_bytes(*parts))),
                            "/".join(parts) + " must be plain ASCII (cmd.exe and PowerShell 5.1 read the OEM/ANSI code page)")

    def test_pinned_python_download_is_consistent(self):
        ps1 = _bytes("scripts", "get-python-windows.ps1").decode("ascii")
        self.assertRegex(ps1, r'\$Version\s*=\s*"3\.\d+\.\d+"')
        self.assertRegex(ps1, r'\$Sha256\s*=\s*"[0-9A-F]{64}"')
        self.assertIn("https://www.python.org/ftp/python/", ps1)


class DemoEndToEndTests(unittest.TestCase):
    """Starts `python run.py --demo` like a user would and talks to it over HTTP."""

    def get(self, port, path, method="GET", body=None, token=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-OBD-Token"] = token
        c.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = c.getresponse()
        data = r.read().decode("utf-8")
        c.close()
        return r.status, data

    def test_demo_starts_reads_and_stops(self):
        port = _free_port()
        data_dir = tempfile.mkdtemp(prefix="obd2dash-e2e-")
        env = dict(os.environ, OBD2DASH_DATA=data_dir, PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.Popen([sys.executable, "run.py", "--demo", "--no-browser", "--port", str(port), "--car", "Honda Fit 2011"],
                                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        try:
            state = None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self.fail("the launcher exited early:\n" + proc.stdout.read())
                try:
                    status, body = self.get(port, "/api/state")
                    if status == 200:
                        state = json.loads(body)
                        if state["status"] == "online":
                            break
                except (OSError, ValueError):
                    pass
                time.sleep(0.3)
            self.assertIsNotNone(state, "server never answered")
            self.assertEqual(state["status"], "online", state)
            self.assertTrue(state["demo"])

            status, html = self.get(port, "/")
            self.assertEqual(status, 200)
            token = re.search(r'name="obd-token" content="([^"]+)"', html).group(1)
            self.assertTrue(token and token != "__TOKEN__")

            status, ports = self.get(port, "/api/ports")
            self.assertEqual(json.loads(ports)["car"], "Honda Fit 2011")

            status, read = self.get(port, "/api/dtc/read", "POST", {}, token)
            self.assertEqual(status, 200, read)
            stored = [c["code"] for c in json.loads(read)["result"]["stored"]]
            self.assertEqual(stored, ["P0420", "P0171"])
        finally:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
            shutil.rmtree(data_dir, ignore_errors=True)
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
