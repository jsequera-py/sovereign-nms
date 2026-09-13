"""
Identity health check — detect the silent device merge.

resolve_device() refuses to auto-merge on a multi-match: it logs and picks
the strongest claim. But when exactly ONE existing device matches, no warning
fires at all, and the claim-attachment loop then writes EVERY claim from the
absorbed device onto the absorbing one. What is left behind is a device row
carrying two distinct values for an identifier that is unique per box.

Nothing raises when it happens; the data is simply wrong. Nine prior bugs in
this project reported success while writing wrong data, which is why the
instrument exists before the fix does.

That signature is check A. It misses the sysname-only merge, measured on
hardware 2026-09-13: the unmatched chassis claim is DISCARDED during
resolution rather than stored, so the absorbing row ends up carrying one
chassis_id, not two. Check B reads link_evidence, which keeps what identity
threw away — a peer chassis a reporter observed and the peer does not own.

CHECKED      A: chassis_id, serial — unique per physical box.
             B: an observed peer_chassis that the peer does not store, where
             that peer already carries a chassis_id of its own.
NOT CHECKED  base_mac as a duplicate signal. A device with two NICs
             legitimately carries two hardware MACs; flagging that would cry
             wolf on the first multi-homed box. mgmt_ip is corroborating only
             and can never establish identity.

BLIND SPOT, narrower than it was. A merge where NEITHER device ever carried a
chassis is still invisible: with nothing stored there is nothing for an
observation to contradict, and check B excludes those peers deliberately to
stay quiet on vendors that omit lldpLocChassisId (RouterOS does). They are
reported as informational lines, not violations. GENERIC_SYSNAMES remains the
only guard for that case.

Run:
    .venv/bin/python scripts/check_identity.py
    .venv/bin/python scripts/check_identity.py --selftest    # both checks
    .venv/bin/python scripts/check_identity.py --json

Exit 0 clean · 1 either check found rows or either selftest failed
       · 2 usage/connection error.
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

# Types a chassis value could legitimately be stored under. Used only to ask
# "does the peer own this identifier at all", never to resolve identity.
CHASSIS_MATCH_TYPES = ["chassis_id", "serial", "base_mac"]

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

# Check B — a peer chassis that was observed and never stored.
#
# An evidence row exists only if both endpoint devices exist, so the peer
# resolved to SOME device. If that device does not carry the chassis the
# reporter saw, the claim was discarded during resolution. That is the
# sysname-only merge.
#
# The EXISTS clause on chassis_id is what makes this precise rather than
# noisy. A device that never advertises its own chassis — RouterOS omits
# lldpLocChassisId, and profile routeros_partial reproduces it — always
# produces an orphan, legitimately. Requiring the peer to already hold a
# DIFFERENT chassis_id narrows this to two observers asserting two chassis
# for one box with one of them dropped. Measured 2026-09-13: br-rtr-02 is
# the legitimate case and is excluded; a merged device is caught.

ORPHAN_SQL = """
WITH ev AS (
  SELECT e.source::text                        AS source,
         e.observed_at,
         lower(e.raw_claim->>'peer_chassis')   AS peer_chassis,
         e.reporter_device_id,
         CASE WHEN l.device_a = e.reporter_device_id THEN l.device_b
              ELSE l.device_a END              AS peer_device_id
    FROM link_evidence e
    JOIN link l ON l.link_id = e.link_id
   WHERE l.tenant_id = %s
     AND e.raw_claim->>'peer_chassis' IS NOT NULL
     AND e.reporter_device_id IS NOT NULL
)
SELECT p.display_name         AS peer,
       p.device_id::text      AS peer_device_id,
       r.display_name         AS reporter,
       ev.peer_chassis        AS observed,
       ev.source,
       ev.observed_at,
       (SELECT array_agg(i.id_value ORDER BY i.id_value)
          FROM device_identity i
         WHERE i.device_id = ev.peer_device_id
           AND i.tenant_id = %s
           AND i.id_type = 'chassis_id')       AS stored
  FROM ev
  JOIN device p ON p.device_id = ev.peer_device_id
  LEFT JOIN device r ON r.device_id = ev.reporter_device_id
 WHERE NOT EXISTS (SELECT 1 FROM device_identity i
                    WHERE i.device_id = ev.peer_device_id
                      AND i.tenant_id = %s
                      AND i.id_type = ANY(%s)
                      AND lower(i.id_value) = ev.peer_chassis)
   AND EXISTS (SELECT 1 FROM device_identity i
                WHERE i.device_id = ev.peer_device_id
                  AND i.tenant_id = %s
                  AND i.id_type = 'chassis_id')
 ORDER BY peer, observed
"""

# Informational, never a violation. A peer carrying no chassis_id of its
# own. Legitimate for vendors that omit lldpLocChassisId. Counted so the
# exclusion in check B is visible rather than silent.

INFO_SQL = """
WITH ev AS (
  SELECT lower(e.raw_claim->>'peer_chassis')   AS peer_chassis,
         e.reporter_device_id,
         CASE WHEN l.device_a = e.reporter_device_id THEN l.device_b
              ELSE l.device_a END              AS peer_device_id
    FROM link_evidence e
    JOIN link l ON l.link_id = e.link_id
   WHERE l.tenant_id = %s
     AND e.raw_claim->>'peer_chassis' IS NOT NULL
     AND e.reporter_device_id IS NOT NULL
)
SELECT DISTINCT p.display_name AS peer, ev.peer_chassis AS observed
  FROM ev JOIN device p ON p.device_id = ev.peer_device_id
 WHERE NOT EXISTS (SELECT 1 FROM device_identity i
                    WHERE i.device_id = ev.peer_device_id
                      AND i.tenant_id = %s
                      AND i.id_type = ANY(%s)
                      AND lower(i.id_value) = ev.peer_chassis)
   AND NOT EXISTS (SELECT 1 FROM device_identity i
                    WHERE i.device_id = ev.peer_device_id
                      AND i.tenant_id = %s
                      AND i.id_type = 'chassis_id')
 ORDER BY peer
"""


def violations(conn, tenant: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(VIOLATION_SQL, (tenant, UNIQUE_PER_BOX))
        return [dict(r) for r in cur.fetchall()]


def orphans(conn, tenant: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(ORPHAN_SQL,
                    (tenant, tenant, tenant, CHASSIS_MATCH_TYPES, tenant))
        return [dict(r) for r in cur.fetchall()]


def unowned_no_chassis(conn, tenant: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(INFO_SQL, (tenant, tenant, CHASSIS_MATCH_TYPES, tenant))
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


def selftest_b(conn, tenant: str) -> bool:
    """
    Prove check B fires before trusting a clean result from it.

    Rewrites one evidence row's peer_chassis to a value nobody owns, re-runs
    the check, and rolls back. The target peer must already carry exactly one
    chassis_id — a peer with none is the legitimate case check B excludes, and
    a peer already orphaned would report a false failure.
    """
    before = {(r["peer_device_id"], r["observed"])
              for r in orphans(conn, tenant)}

    with conn.cursor() as cur:
        cur.execute(
            """SELECT e.evidence_id::text AS evidence_id,
                      CASE WHEN l.device_a = e.reporter_device_id
                           THEN l.device_b ELSE l.device_a END::text
                          AS peer_device_id
                 FROM link_evidence e
                 JOIN link l ON l.link_id = e.link_id
                WHERE l.tenant_id = %s
                  AND e.raw_claim->>'peer_chassis' IS NOT NULL
                  AND e.reporter_device_id IS NOT NULL
                  AND (SELECT count(DISTINCT i.id_value)
                         FROM device_identity i
                        WHERE i.device_id = CASE
                                  WHEN l.device_a = e.reporter_device_id
                                  THEN l.device_b ELSE l.device_a END
                          AND i.tenant_id = %s
                          AND i.id_type = 'chassis_id') = 1
                LIMIT 1""",
            (tenant, tenant))
        row = cur.fetchone()
    if not row:
        print("  SELFTEST B INCONCLUSIVE: no evidence row whose peer carries "
              "exactly one chassis_id.")
        return False

    target_evidence = row["evidence_id"]
    target_peer = row["peer_device_id"]
    if (target_peer, SELFTEST_VALUE) in before:
        print("  SELFTEST B INCONCLUSIVE: chosen peer is already orphaned.")
        return False

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE link_evidence
                  SET raw_claim = jsonb_set(raw_claim, '{peer_chassis}',
                                            to_jsonb(%s::text))
                WHERE evidence_id = %s""",
            (SELFTEST_VALUE, target_evidence))

    after = {(r["peer_device_id"], r["observed"])
             for r in orphans(conn, tenant)}
    conn.rollback()
    restored = {(r["peer_device_id"], r["observed"])
                for r in orphans(conn, tenant)}

    fired = (target_peer, SELFTEST_VALUE) in after
    clean = restored == before
    print(f"  synthetic orphan chassis on {target_peer[:8]} -> "
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
        orphaned = orphans(conn, tenant)
        no_chassis = unowned_no_chassis(conn, tenant)
        ctx = context(conn, tenant)

        if args.json:
            print(json.dumps({"violations": found,
                              "orphan_peer_chassis": orphaned,
                              "peers_without_chassis": no_chassis,
                              **ctx}, indent=2, default=str))
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
                print("  (check A alone cannot see a merge that leaves one "
                      "chassis; check B below covers the contradicted case)")
            print(f"  ORPHAN PEER CHASSIS     : {len(orphaned)}")
            for o in orphaned:
                stored = ", ".join(o["stored"] or []) or "none"
                print(f"    {o['peer']} was seen as {o['observed']} by "
                      f"{o['reporter'] or 'unknown'} ({o['source']})")
                print(f"        but stores: {stored}")
            if not orphaned:
                print("  clean — every observed peer chassis is stored")
            for u in no_chassis:
                print(f"  info: {u['peer']} carries no chassis_id of its own "
                      f"(seen as {u['observed']}) — excluded from check B")
            print("=" * 62)

        rc = 1 if found or orphaned else 0

        if args.selftest:
            print("  selftest:")
            if not selftest(conn, tenant):
                print("  SELFTEST FAILED — the check cannot be trusted")
                rc = 1
            else:
                print("  selftest passed — the check fires on a real duplicate")
            if not selftest_b(conn, tenant):
                print("  SELFTEST B FAILED — check B cannot be trusted")
                rc = 1
            else:
                print("  selftest B passed — check B fires on a dropped claim")

    return rc


if __name__ == "__main__":
    sys.exit(main())
