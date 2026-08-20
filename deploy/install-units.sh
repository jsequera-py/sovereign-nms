#!/usr/bin/env bash
#
# deploy/install-units.sh — keep /etc/systemd/system in sync with deploy/.
#
# deploy/ is the source of truth for every unit this project ships. Editing a
# unit in the repo does NOT change the running system, and nothing warns you.
# This closes that gap the way host-setup.sh closed the snmpd one.
#
# IDEMPOTENT. Copies nothing and reloads nothing when the installed units
# already match, so a run against a synced host is a true no-op and is safe
# while polling is live.
#
# NEVER restarts anything automatically. It reports what needs a restart and
# stops. Restarting nms-api.service mid-cycle would drop a collector POST, and
# a dropped POST is indistinguishable from a dead collector in
# /v1/health.minutes_since_last_run. A monitoring system must not manufacture
# its own false alarms.
#
# What a changed unit file actually requires:
#   *.timer             daemon-reload; systemd recomputes the next elapse
#   oneshot *.service   nothing - the next timer fire uses the new file
#   long-running        explicit restart, listed at the end for a human
#
# Usage:
#   sudo ./deploy/install-units.sh --check   report drift, change nothing
#   sudo ./deploy/install-units.sh           copy changed units, daemon-reload
#
# Exit: 0 synced or applied · 1 error · 3 drift found (--check only)

set -uo pipefail

DEPLOY_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
SYSTEMD_DIR=/etc/systemd/system
STAMP=$(date +%Y%m%d%H%M%S)

# Long-running units: a changed file only takes effect on restart.
LONG_RUNNING="nms-api.service"

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

changed=0
drift=0
fail=0
restart_list=""

ok()   { printf 'OK      %s\n' "$*"; }
need() { printf 'NEED    %s\n' "$*"; drift=1; }
did()  { printf 'APPLIED %s\n' "$*"; changed=1; }
err()  { printf 'ERROR   %s\n' "$*" >&2; fail=1; }
warn() { printf 'WARN    %s\n' "$*"; }

mark_restart() {
    case " $LONG_RUNNING " in
        *" $1 "*) restart_list="$restart_list $1" ;;
    esac
}

if [ "$(id -u)" -ne 0 ]; then
    err "must run as root: sudo $0 ${1:-}"
    exit 1
fi

shopt -s nullglob
units=("$DEPLOY_DIR"/*.service "$DEPLOY_DIR"/*.timer)
shopt -u nullglob

if [ "${#units[@]}" -eq 0 ]; then
    err "no .service or .timer files found in $DEPLOY_DIR"
    exit 1
fi

for src in "${units[@]}"; do
    name=$(basename "$src")
    dst="$SYSTEMD_DIR/$name"

    if [ ! -f "$dst" ]; then
        if [ "$CHECK" -eq 1 ]; then
            need "$name is not installed in $SYSTEMD_DIR"
        else
            install -m 0644 "$src" "$dst" && did "installed $name" \
                || err "could not install $name"
            mark_restart "$name"
        fi
    elif cmp -s "$src" "$dst"; then
        ok "$name in sync"
    elif [ "$CHECK" -eq 1 ]; then
        need "$name differs from the installed copy"
    else
        cp -a "$dst" "$dst.bak-$STAMP"
        install -m 0644 "$src" "$dst" \
            && did "updated $name (previous saved as $name.bak-$STAMP)" \
            || err "could not update $name"
        mark_restart "$name"
    fi
done

[ "$fail" -eq 1 ] && exit 1

if [ "$changed" -eq 1 ]; then
    systemctl daemon-reload || err "daemon-reload failed"
    [ "$fail" -eq 1 ] && exit 1
    ok "systemctl daemon-reload done"
else
    echo "no changes made - nothing copied, nothing reloaded"
fi

# --- enabled state (read-only, informational) ---------------------------
for src in "${units[@]}"; do
    name=$(basename "$src")
    case "$name" in
    *.timer)
        state=$(systemctl is-enabled "$name" 2>&1)
        if [ "$state" = "enabled" ]; then
            ok "$name is enabled"
        else
            warn "$name is '$state' - it will not fire until enabled:"
            warn "        sudo systemctl enable --now $name"
        fi
        ;;
    esac
done

if [ -n "$restart_list" ]; then
    echo
    echo "RESTART REQUIRED - deliberately not done for you. Pick your moment:"
    for u in $restart_list; do
        echo "        sudo systemctl restart $u"
    done
fi

if [ "$CHECK" -eq 1 ] && [ "$drift" -eq 1 ]; then
    echo "DRIFT: installed units do not match deploy/"
    exit 3
fi
echo "done"
exit 0
