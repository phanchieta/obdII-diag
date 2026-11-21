# Third-party notices

OBD-II Diag itself is MIT licensed (see [LICENSE](LICENSE)). It bundles or uses the following third-party material.

## pySerial 3.5 — BSD-3-Clause

- Where: `vendor/serial/` (unmodified), license text in [`vendor/LICENSE-pyserial.txt`](vendor/LICENSE-pyserial.txt)
- Why: serial-port access for USB and Bluetooth (virtual COM port) adapters. It is bundled so that nothing has to be installed with `pip`.
- Project: https://github.com/pyserial/pyserial — Copyright (c) 2001-2020 Chris Liechti

## DTC database (generic SAE J2012 code titles) — MIT

- Where: [`obd2dash/kb/sae_titles.en.txt`](obd2dash/kb/sae_titles.en.txt), license text in [`obd2dash/kb/LICENSE-dtc-database.txt`](obd2dash/kb/LICENSE-dtc-database.txt)
- Why: English titles for about 8,000 generic trouble codes, used as a fallback when a code has no hand-written guidance.
- Project: https://github.com/Wal33D/dtc-database — Copyright (c) 2024 Wal33D (Waleed Judah)

## Python for Windows (downloaded on demand, not part of this repository)

- `run.bat` can offer to download the official *embeddable* Python package from python.org into `runtime/` (see `scripts/get-python-windows.ps1`).
  The download is verified against a pinned SHA-256 and the Python Software Foundation signature. It is covered by the
  [Python Software Foundation License](https://docs.python.org/3/license.html) and is excluded from git (`.gitignore`).

## Hand-written trouble-code guidance

The explanations in `obd2dash/kb/curated.*.txt` were written for this project (MIT). They are general guidance, not
manufacturer service information.
