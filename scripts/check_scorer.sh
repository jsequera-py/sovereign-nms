#!/usr/bin/env bash
#
# One graded cycle: stop the collector, reset, poll, grade, restore.
#
# The timer is stopped for the whole destructive window. A cycle firing
# between reset_data.sh and poll_cycle.sh writes into a half-empty database.
# The accidental wipe of 2026-09-01 proved an interactive prompt is not an
# interlock, and --yes removes even that.
#
# The restore runs from a trap, so a failed gate cannot leave polling off.
# Prior state is captured rather than assumed: a 24-hour exit test stops the
# timer deliberately, and this script must not turn it back on.
#
# Needs sudo, like `make check`.

set -uo pipefail

REPO=/home/jsequera/nms
PY="$REPO/.venv/bin/python"
LOCK="$REPO/.exit-test-running"
TIMER=nms-collector.timer

cd "$REPO" || exit 1

if [ -e "$LOCK" ]; then
    echo "check-scorer: refusing to run - $LOCK exists, a 24-hour exit test is in progress." >&2
    exit 1
fi

WAS_ACTIVE=0
systemctl is-active --quiet "$TIMER" && WAS_ACTIVE=1

restore() {
    if [ "$WAS_ACTIVE" = "1" ]; then
        echo "check-scorer: restarting $TIMER"
        sudo systemctl start "$TIMER" \
            || echo "check-scorer: WARNING - could not restart $TIMER" >&2
    else
        echo "check-scorer: $TIMER was not running before; leaving it stopped"
    fi
}
trap restore EXIT

if [ "$WAS_ACTIVE" = "1" ]; then
    echo "check-scorer: stopping $TIMER for the destructive window"
    sudo systemctl stop "$TIMER" || {
        echo "check-scorer: could not stop $TIMER, refusing to reset" >&2
        exit 1
    }
fi

./scripts/reset_data.sh --yes || exit 1
./scripts/poll_cycle.sh       || exit 1
"$PY" scripts/gate.py
exit $?
