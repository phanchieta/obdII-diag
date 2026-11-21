#!/bin/sh
# Try OBD-II Diag without a car: starts the dashboard with the built-in simulator.
exec "$(dirname "$0")/run.sh" --demo "$@"
