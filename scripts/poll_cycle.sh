#!/usr/bin/env bash
#
# One collector cycle: simulated fleet first, then real hardware.
#
# Exists because two ExecStart= lines in a Type=oneshot unit abort on the
# first failure. That would let a missing inventory.generated.yaml (gitignored,
# produced by sim/genfleet.py) stop the MikroTik and the OptiPlex from being
# polled at all. Both polls run here regardless; the script still exits
# non-zero if either failed, so a bad cycle fails the unit and shows up in
# `systemctl list-timers` / `journalctl -u nms-collector`.
#
# Sequential on purpose. The two inventories can resolve to the same device row
# (optiplex / optiplex-replay), and concurrent identity resolution has never
# been tested. Do not parallelise these without exercising that path.
#
# Deliberately no `set -e` - the whole point is to continue past a failure.

set -uo pipefail

REPO=/home/jsequera/nms
PY="$REPO/.venv/bin/python"

cd "$REPO" || exit 1

rc=0

# Simulated fleet - keeps the scorer meaningful and its evidence fresh.
if [ -f inventory.generated.yaml ]; then
    "$PY" -m collector.poll --inventory inventory.generated.yaml || rc=1
else
    echo "inventory.generated.yaml is missing - run: $PY sim/genfleet.py" >&2
    rc=1
fi

# Real hardware - MikroTik and OptiPlex.
"$PY" -m collector.poll --inventory inventory.yaml || rc=1

exit "$rc"
