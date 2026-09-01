


#!/usr/bin/env bash
# Wipe collected data, keep schema and tenant.
#
# metric_sample is deleted explicitly: entity_id is an untyped UUID with
# no foreign key (it points at either a device or an interface), so it
# is NOT reached by CASCADE. Without this, orphaned metrics accumulate
# and reference entities that no longer exist.
set -euo pipefail

DB_URL="${DB_URL:-postgresql://nms:nms_dev_only@127.0.0.1:5432/nms}"

ASSUME_YES=0
[ "${1:-}" = "--yes" ] && ASSUME_YES=1
[ "${RESET_ASSUME_YES:-0}" = "1" ] && ASSUME_YES=1

if [ "$ASSUME_YES" != "1" ]; then
    read -rp "Delete all devices, interfaces, links and metrics? [y/N] " ans
    [ "$ans" = "y" ] || { echo "aborted"; exit 0; }
fi

psql "$DB_URL" -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
DELETE FROM metric_sample;
DELETE FROM dependency;
DELETE FROM link_evidence;
DELETE FROM link;
DELETE FROM interface;
DELETE FROM device_identity;
DELETE FROM device_reachability_change;
DELETE FROM device_reachability;
DELETE FROM device;
DELETE FROM discovery_run;
COMMIT;
SELECT 'devices'      AS table, count(*) FROM device
UNION ALL SELECT 'interfaces', count(*) FROM interface
UNION ALL SELECT 'identities', count(*) FROM device_identity
UNION ALL SELECT 'metrics',    count(*) FROM metric_sample
UNION ALL SELECT 'reachability', count(*) FROM device_reachability
UNION ALL SELECT 'reach_changes', count(*) FROM device_reachability_change
UNION ALL SELECT 'tenants',    count(*) FROM tenant;
SQL
