#!/usr/bin/env bash
set -euo pipefail

DB_URL="${DB_URL:-postgresql://nms:nms_dev_only@127.0.0.1:5432/nms}"
MIG_DIR="$(cd "$(dirname "$0")/../migrations" && pwd)"

psql "$DB_URL" -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migration (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SQL

for f in $(ls "$MIG_DIR"/*.sql | sort); do
    base=$(basename "$f")
    applied=$(psql "$DB_URL" -tAc "SELECT 1 FROM schema_migration WHERE filename='$base'")
    if [ "$applied" = "1" ]; then
        echo "skip  $base"
        continue
    fi
    echo "apply $base"
    psql "$DB_URL" -v ON_ERROR_STOP=1 -q -f "$f"
    psql "$DB_URL" -q -c "INSERT INTO schema_migration (filename) VALUES ('$base')"
done

echo "migrations complete"
