#!/usr/bin/env bash
#
# deploy/host-setup.sh — host configuration that git cannot otherwise capture.
#
# Makes net-snmp serve the LLDP-MIB via lldpd's AgentX subagent. Without this,
# lldpd sees neighbours but net-snmp does not export them, and the collector
# reads zero neighbours from this host. A rebuilt OptiPlex loses the setting
# silently, which is why it lives here rather than only in HANDOFF.md.
#
# IDEMPOTENT. Restarts snmpd/lldpd ONLY if it actually changed a file, so a run
# against an already-configured host is a true no-op and is safe while polling
# is live. A restart costs a ~30s window where lldpd's neighbour table is empty.
#
# REFUSES rather than guesses. A conflicting existing directive stops the run.
#
# Usage:
#   sudo ./deploy/host-setup.sh --check   report drift, change nothing
#   sudo ./deploy/host-setup.sh           apply, restart if needed, verify
#
# Exit: 0 compliant or applied · 1 error/refused · 3 drift found (--check only)

set -uo pipefail

SNMPD_CONF=/etc/snmp/snmpd.conf
LLDPD_DEFAULT=/etc/default/lldpd
AGENTX_PERMS='agentXPerms 0660 0550 Debian-snmp _lldpd'
LLDPD_ARGS='DAEMON_ARGS="-x"'
LLDP_MIB=1.0.8802.1.1.2
REM_SYSNAME_RE='^iso\.0\.8802\.1\.1\.2\.1\.4\.1\.1\.9\.'
COMMUNITY="${SNMP_COMMUNITY:-public}"
STAMP=$(date +%Y%m%d%H%M%S)

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

changed=0
drift=0
fail=0

ok()   { printf 'OK      %s\n' "$*"; }
need() { printf 'NEED    %s\n' "$*"; drift=1; }
did()  { printf 'APPLIED %s\n' "$*"; changed=1; }
err()  { printf 'ERROR   %s\n' "$*" >&2; fail=1; }
warn() { printf 'WARN    %s\n' "$*"; }

backup_once() { [ -f "$1.bak-$STAMP" ] || cp -a "$1" "$1.bak-$STAMP"; }

if [ "$(id -u)" -ne 0 ]; then
    err "must run as root: sudo $0 ${1:-}"
    exit 1
fi

# --- snmpd: master agentx -----------------------------------------------
if [ ! -f "$SNMPD_CONF" ]; then
    err "$SNMPD_CONF not found - is snmpd installed?"
elif grep -qE '^[[:space:]]*master[[:space:]]+agentx' "$SNMPD_CONF"; then
    ok "master agentx present"
elif [ "$CHECK" -eq 1 ]; then
    need "master agentx missing from $SNMPD_CONF"
else
    backup_once "$SNMPD_CONF"
    printf '\n# --- sovereign-nms (host-setup.sh)\nmaster agentx\n' >> "$SNMPD_CONF"
    did "added 'master agentx' to $SNMPD_CONF"
fi

# --- snmpd: agentXPerms -------------------------------------------------
if [ -f "$SNMPD_CONF" ]; then
    existing=$(grep -m1 -E '^[[:space:]]*agentXPerms' "$SNMPD_CONF")
    if [ -z "$existing" ]; then
        if [ "$CHECK" -eq 1 ]; then
            need "agentXPerms missing from $SNMPD_CONF"
        else
            backup_once "$SNMPD_CONF"
            printf '# lets lldpd (_lldpd) reach the AgentX socket\n%s\n' \
                   "$AGENTX_PERMS" >> "$SNMPD_CONF"
            did "added agentXPerms to $SNMPD_CONF"
        fi
    elif [ "$(printf '%s' "$existing" | tr -s ' ')" = "$AGENTX_PERMS" ]; then
        ok "agentXPerms already correct"
    else
        err "conflicting agentXPerms in $SNMPD_CONF"
        err "  found:  $existing"
        err "  wanted: $AGENTX_PERMS"
        err "refusing to guess - reconcile by hand"
    fi
fi

# --- lldpd: DAEMON_ARGS -------------------------------------------------
if [ ! -f "$LLDPD_DEFAULT" ]; then
    err "$LLDPD_DEFAULT not found - is lldpd installed?"
else
    existing=$(grep -m1 -E '^[[:space:]]*DAEMON_ARGS=' "$LLDPD_DEFAULT")
    case "$existing" in
        "")
            if [ "$CHECK" -eq 1 ]; then
                need "DAEMON_ARGS missing from $LLDPD_DEFAULT"
            else
                backup_once "$LLDPD_DEFAULT"
                printf '# -x enables the AgentX subagent (sovereign-nms)\n%s\n' \
                       "$LLDPD_ARGS" >> "$LLDPD_DEFAULT"
                did "added $LLDPD_ARGS to $LLDPD_DEFAULT"
            fi
            ;;
        *-x*)
            ok "lldpd DAEMON_ARGS already has -x"
            ;;
        *)
            err "existing DAEMON_ARGS in $LLDPD_DEFAULT lacks -x:"
            err "  found: $existing"
            err "appending a second line would shadow it - merge by hand"
            ;;
    esac
fi

[ "$fail" -eq 1 ] && exit 1

# --- restart ONLY if we changed something -------------------------------
if [ "$changed" -eq 1 ]; then
    echo "restarting snmpd then lldpd (master first)"
    systemctl restart snmpd || err "snmpd restart failed"
    sleep 2
    systemctl restart lldpd || err "lldpd restart failed"
    [ "$fail" -eq 1 ] && exit 1
    echo "waiting 35s for lldpd to repopulate its neighbour table"
    sleep 35
else
    echo "no changes made - nothing restarted"
fi

# --- verify (read-only, always safe) ------------------------------------
#
# Do NOT pipe snmpwalk into `grep -q`. grep -q exits on the first match,
# snmpwalk takes SIGPIPE and exits 141, and `set -o pipefail` promotes that
# to the pipeline's status - reporting a broken host when it is healthy.
# That bug was in the first version of this script. Capture, then test.
if ! command -v snmpwalk >/dev/null 2>&1; then
    warn "snmpwalk not installed - skipping verification"
else
    walk=$(snmpwalk -v2c -c "$COMMUNITY" 127.0.0.1 "$LLDP_MIB" 2>&1)
    rc=$?
    if [ "$rc" -ne 0 ] || [ -z "$walk" ]; then
        warn "LLDP-MIB walk failed (rc=$rc, community=$COMMUNITY). It said:"
        head -3 <<< "$walk" | sed 's/^/        /'
    elif [ "${walk#*No Such Object}" != "$walk" ]; then
        warn "LLDP-MIB subtree is empty. A freshly restarted lldpd has no"
        warn "neighbours for up to 30 seconds - wait, then re-run --check."
    else
        oids=$(wc -l <<< "$walk")
        nbrs=$(grep -cE "$REM_SYSNAME_RE" <<< "$walk")
        ok "net-snmp is serving the LLDP-MIB ($oids OIDs)"
        ok "LLDP remote neighbours visible: $nbrs"
    fi
fi

if [ "$CHECK" -eq 1 ] && [ "$drift" -eq 1 ]; then
    echo "DRIFT: configuration is not what this script expects"
    exit 3
fi
echo "done"
exit 0
