#!/usr/bin/env python3
"""Start OBD-II Diag (works on Windows and Linux).

    python run.py                 connect to the adapter automatically and open the dashboard
    python run.py --demo          car simulator, no adapter needed
    python run.py --car "Honda Fit 2011" --port 8765 --com COM5 --no-browser

Needs Python 3.8 or newer and nothing else (pyserial is bundled in ./vendor).
"""
import os
import sys

if sys.version_info < (3, 8):   # keep this file free of newer syntax so that old interpreters reach this message
    sys.stderr.write(
        "OBD-II Diag needs Python 3.8 or newer (this is Python %d.%d).\n"
        "O OBD-II Diag precisa do Python 3.8 ou mais novo (este e o Python %d.%d).\n"
        % (sys.version_info[0], sys.version_info[1], sys.version_info[0], sys.version_info[1]))
    sys.exit(1)

# The embeddable Windows Python ignores the script folder, so add it explicitly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from obd2dash.__main__ import main  # noqa: E402


def _args(argv):
    """Connect automatically unless the user chose something else (--demo, --com, --auto, help)."""
    chosen = ("--demo", "--auto", "--com", "-h", "--help")
    if any(a in chosen or a.startswith("--com=") for a in argv):
        return list(argv)
    return ["--auto"] + list(argv)


if __name__ == "__main__":
    sys.exit(main(_args(sys.argv[1:])))
