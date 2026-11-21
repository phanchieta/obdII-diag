#!/bin/sh
# OBD-II Diag launcher for Linux (and macOS, untested).
#   ./run.sh                  connect to the adapter automatically and open the dashboard
#   ./run.sh --demo           car simulator, no adapter needed
#   ./run.sh --car "Honda Fit 2011" --port 8765 --com /dev/ttyUSB0 --no-browser
# Needs Python 3.8 or newer. Nothing else to install (pyserial is bundled in ./vendor).
cd "$(dirname "$0")" || exit 1

found=""
for py in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python; do
  command -v "$py" >/dev/null 2>&1 || continue
  if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    exec "$py" run.py "$@"
  fi
  if [ -z "$found" ]; then
    found="$py $("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
  fi
done

echo "OBD-II Diag needs Python 3.8 or newer and none was found on this computer." >&2
[ -n "$found" ] && echo "(only found: $found)" >&2
echo "O OBD-II Diag precisa do Python 3.8 ou mais novo e nenhum foi encontrado neste computador." >&2
echo "" >&2
echo "Install it with your package manager / Instale com o gerenciador de pacotes:" >&2
echo "  Debian / Ubuntu / Mint:  sudo apt install python3" >&2
echo "  Fedora:                  sudo dnf install python3" >&2
echo "  Arch / Manjaro:          sudo pacman -S python" >&2
echo "  openSUSE:                sudo zypper install python3" >&2
exit 1
