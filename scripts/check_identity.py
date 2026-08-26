#!/usr/bin/env python3
"""
Identity health check — detect the silent device merge.

resolve_device() refuses to auto-merge on a multi-match: it logs and picks
the strongest claim. But when exactly ONE existing device matches, no warning
fires at all, and the claim-attachment loop then writes EVERY claim from the
absorbed device onto the absorbing one. What is left behind is a device row
carrying two distinct values for an identifier that is unique per box.

That signature is what this checks. Nothing raises when it happens; the data
is simply wrong. Nine prior bugs in this project reported success while
writing wrong data, which is why the instrument exists before the fix does.

CHECKED      chassis_id, serial — unique per physical box.
NOT CHECKED  base_mac. A device with two NICs legitimately carries two
             hardware MACs; flagging that would cry wolf on the first
             multi-homed box. mgmt_ip is corroborating only and can never
             establish identity.

BLIND SPOT, deliberate and worth knowing. Two devices whose ONLY resolving
claim is a sysName merge into one device carrying ZERO chassis claims, and
this check cannot see it. GENERIC_SYSNAMES is the only guard there. A clean
run here does not mean merges are impossible.

Run:
    .venv/bin/python scripts/check_identity.py
    .venv/bin/python scripts/check_identity.py --selftest
    .venv/bin/python scripts/check_identity.py --json

Exit 0 clean · 1 violation or failed selftest · 2 usage/connection error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# Identifiers that name one physical box. Two distinct values of one of
# these on a single device row means two boxes were collapsed into one.
UNIQUE_PER_BOX = ["chassis_id", "serial"]

# A locally-administered MAC nobody will ever really own. Used only by
# --selftest, and always rolled back.
SELFTEST_VALUE = "00:00:5e:00:53:99"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


VIOLATION_SQL = """
SELECT d.display_name,
       i.id_type,
       count(DISTINCT i.id_value)     AS distinct_values,
       array_agg(DISTINCT i.id_value) AS id_values,
       d.device_id::text              AS device_id
  FROM device_identity i
  JOIN device d ON d.device_id = i.device_id
 WHERE i.tenant_id = %s
   AND i.device_id IS NOT NULL
   AND i.id_type = ANY(%s)
 GROUP BY d.device_id, d.display_name, i.id_type
HAVING count(DISTINCT i.id_value) > 1
 ORDER BY d.display_name, i.id_type
"""


def violations(conn, tenant: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(VIOLATION_SQL, (tenant, UNIQUE_PER_BOX))
        return [dict(r) for r in cur.fetchall()]


def context(conn, tenant: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM device WHERE tenant_id = %s",
                    (tenant,))
        devices = cur.fetchone()["n"]
        cur.execute(
            """SELECT count(DISTINCT device_id) AS n
                 FROM device_identity
                WHERE tenant_id = %s AND device_id IS NOT NULL
                  AND id_type = ANY(%s)""",
            (tenant, UNIQUE_PER_BOX))
        identified = cur.fetchone()["n"]
    return {"devices": devices, "devices_with_hard_identity": identified}


def selftest(conn, tenant: str) -> bool:
    """
    Prove the query fires before trusting a clean result from it.

    Injects a second chassis_id against a device that currently has exactly
    one, re-runs the check, and rolls back. Picking a device that is already
    in violation would report a false failure, so the target is constrained.
    A check never seen to fail is not evidence of anything.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT device_id FROM device_identity
                WHERE tenant_id = %s AND id_type = 'chassis_id'
                  AND device_id IS NOT NULL
                GROUP BY device_id
               HAVING count(DISTINCT id_value) = 1
                LIMIT 1""",
            (tenant,))
        row = cur.fetchone()
    if not row:
        print("  SELFTEST INCONCLUSIVE: no device carries exactly one "
              "chassis_id.")
        return False

    target = str(row["device_id"])
    before = {v["device_id"] for v in violations(conn, tenant)}

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO device_identity
                   (tenant_id, device_id, id_type, id_value, source)
               VALUES (%s, %s, 'chassis_id', %s, 'inferred'::source_kind)""",
            (tenant, target, SELFTEST_VALUE))

    after = {v["device_id"] for v in violations(conn, tenant)}
    conn.rollback()
    restored = {v["device_id"] for v in violations(conn, tenant)}

    fired = target in after and target not in before
    clean = restored == before
    print(f"  synthetic duplicate on {target[:8]} -> "
          f"detected={fired}  rolled_back_clean={clean}")
    return fired and clean


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    env = load_env(Path(args.env))
    dsn = env.get("DB_URL")
    tenant = args.tenant or env.get("TENANT_ID")
    if not dsn or not tenant:
        print("need DB_URL and TENANT_ID in .env, or pass --tenant",
              file=sys.stderr)
        return 2

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        found = violations(conn, tenant)
        ctx = context(conn, tenant)

        if args.json:
            print(json.dumps({"violations": found, **ctx}, indent=2,
                             default=str))
        else:
            print("=" * 62)
            print("  IDENTITY CHECK — silent device merge")
            print("=" * 62)
            print(f"  devices                 : {ctx['devices']}")
            print(f"  with chassis_id/serial  : "
                  f"{ctx['devices_with_hard_identity']}")
            print(f"  VIOLATIONS              : {len(found)}")
            for v in found:
                print(f"    {v['display_name']} carries "
                      f"{v['distinct_values']} distinct {v['id_type']} values")
                for val in v["id_values"]:
                    print(f"        {val}")
            if not found:
                print("  clean — no device carries two hard identities")
                print("  (does NOT rule out a sysname-only merge; see docstring)")
            print("=" * 62)

        rc = 1 if found else 0

        if args.selftest:
            print("  selftest:")
            if not selftest(conn, tenant):
                print("  SELFTEST FAILED — the check cannot be trusted")
                rc = 1
            else:
                print("  selftest passed — the check fires on a real duplicate")

    return rc


if __name__ == "__main__":
    sys.exit(main())
