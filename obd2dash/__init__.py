"""OBD-II Diag: a local web dashboard for ELM327 adapters (Bluetooth / USB)."""
import os
import sys

__version__ = "1.0.0"

# Project root (the folder that contains obd2dash/, web/, vendor/ and data/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Third-party code lives in vendor/ (pyserial, pure Python): nothing needs to be installed with pip.
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
