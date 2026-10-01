"""
Incidents — collapse a cascade into one incident with a root cause.

THE MODEL, in four rules.

1. A device is DOWN if `device_reachability.reach_status` is not 'ok'.
2. Only the vantage point's own component of the topology is in scope. A
   failure elsewhere is reported as out-of-scope, never as collateral. The
   engine must not claim a root cause for something it cannot see from here.
3. An INCIDENT is a connected group of down devices. Two unrelated failures
   produce two incidents with no time-window heuristic and no tuning.
4. Within an incident, a ROOT CAUSE is a down device that still touches a
   device the vantage can reach. Everything else in the group is SUPPRESSED:
   it is down because the root cause is down.

WHY STALE LINKS COUNT. A dead device stops refreshing its evidence, so its
links go `stale` within the 30-minute freshness window. Reading only `active`
links would delete the topology the engine needs at exactly the moment it
needs it. `5c7b3fa` stopped the rollup zeroing confidence for this reason;
the last-known number is the honest one to report.

SCOPED LIMITATION, recorded rather than discovered later. Direction is derived
from the vantage point at query time instead of a stored dependency table.
That is correct for deployment scenario 1: one collector, tree-shaped
topology. It is wrong for a mesh with redundant paths, and for scenario 2
with collectors at several vantage points. A real dependency layer with
direction and confidence is still the right answer for those.

Run:
    .venv/bin/python scripts/incidents.py --vantage srv-mon-01
    .venv/bin/python scripts/incidents.py --vantage srv-mon-01 --json

Exit 0 always. This reports; it does not grade. Grading is the exit test.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


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


def bfs(start: str | None, adj: dict[str, set[str]], skip: set[str]) -> set[str]:
    if not start or start in skip:
        return set()
    seen = {start}
    queue = [start]
    while queue:
        cur = queue.pop()
        for nxt in adj.get(cur, ()):
            if nxt not in seen and nxt not in skip:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def load_world(conn, tenant: str):
    with conn.cursor() as cur:
        cur.execute("""SELECT device_id::text AS id, display_name, state
                         FROM device WHERE tenant_id = %s""", (tenant,))
        devices = {r["id"]: r for r in cur.fetchall()}

        # active AND stale: a dead device's links go stale, and losing them
        # would erase the topology this reasons over.
        cur.execute("""SELECT link_id::text AS link_id,
                              device_a::text AS a, device_b::text AS b,
                              confidence, state, fidelity
                         FROM link
                        WHERE tenant_id = %s AND state IN ('active','stale')""",
                    (tenant,))
        links = [dict(r) for r in cur.fetchall()]

        cur.execute("""SELECT poll_target, device_id::text AS id,
                              reach_status, error, changed_at
                         FROM device_reachability
                        WHERE tenant_id = %s AND reach_status <> 'ok'
                        ORDER BY poll_target""", (tenant,))
        down_rows = [dict(r) for r in cur.fetchall()]

    adj: dict[str, set[str]] = {}
    for l in links:
        adj.setdefault(l["a"], set()).add(l["b"])
        adj.setdefault(l["b"], set()).add(l["a"])
    return devices, links, adj, down_rows


def evidence_for(conn, tenant: str, link_ids: list[str]) -> list[dict]:
    if not link_ids:
        return []
    with conn.cursor() as cur:
        cur.execute("""SELECT e.link_id::text AS link_id, e.source::text AS source,
                              e.observed_at, d.display_name AS reporter
                         FROM link_evidence e
                         LEFT JOIN device d ON d.device_id = e.reporter_device_id
                        WHERE e.link_id = ANY(%s::uuid[])
                        ORDER BY e.observed_at DESC""", (link_ids,))
        return [dict(r) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--vantage", default="srv-mon-01",
                    help="display_name of the device the collector sits on")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    env = load_env(Path(args.env))
    dsn = env.get("DB_URL")
    tenant = args.tenant or env.get("TENANT_ID")
    if not dsn or not tenant:
        print("need DB_URL and TENANT_ID in .env, or pass --tenant",
              file=sys.stderr)
        return 2

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        devices, links, adj, down_rows = load_world(conn, tenant)

        by_name = {d["display_name"]: i for i, d in devices.items()}
        vantage_id = by_name.get(args.vantage)

        name = lambda i: devices[i]["display_name"] if i in devices else i[:8]

        # A target with no device_id was never discovered, so it cannot be
        # placed on the graph at all. Report it, never guess about it.
        unplaceable = [r["poll_target"] for r in down_rows
                       if not r["id"] or r["id"] not in devices]
        down_ids = {r["id"] for r in down_rows
                    if r["id"] and r["id"] in devices}

        result = {
            "vantage": args.vantage,
            "vantage_found": vantage_id is not None,
            "incidents": [],
            "out_of_scope": [],
            "unplaceable": sorted(unplaceable),
            "down_total": len(down_ids) + len(unplaceable),
        }

        if vantage_id is None:
            result["error"] = f"vantage {args.vantage!r} is not a known device"
        elif vantage_id in down_ids:
            result["error"] = ("the vantage point itself is down; nothing "
                               "can be concluded about what sits behind it")
        else:
            in_scope = bfs(vantage_id, adj, set())
            reachable_now = bfs(vantage_id, adj, down_ids)

            result["out_of_scope"] = sorted(
                name(i) for i in down_ids if i not in in_scope)
            scoped = down_ids & in_scope

            # Incidents = connected groups of down devices.
            groups: list[set[str]] = []
            unassigned = set(scoped)
            while unassigned:
                seed = unassigned.pop()
                group = {seed}
                stack = [seed]
                while stack:
                    cur = stack.pop()
                    for nxt in adj.get(cur, ()):
                        if nxt in unassigned:
                            unassigned.discard(nxt)
                            group.add(nxt)
                            stack.append(nxt)
                groups.append(group)

            when = {r["id"]: r["changed_at"] for r in down_rows if r["id"]}
            err = {r["id"]: r["error"] for r in down_rows if r["id"]}

            for group in groups:
                roots = sorted(
                    d for d in group
                    if any(n in reachable_now for n in adj.get(d, ())))
                suppressed = sorted(group - set(roots))
                frontier = [l["link_id"] for l in links
                            if (l["a"] in roots and l["b"] in reachable_now)
                            or (l["b"] in roots and l["a"] in reachable_now)]
                started = min((when[d] for d in group if d in when),
                              default=None)
                result["incidents"].append({
                    "root_cause": [name(d) for d in roots],
                    "suppressed": sorted(name(d) for d in suppressed),
                    "devices_affected": len(group),
                    "alerts_avoided": len(suppressed),
                    "started_at": started.isoformat() if started else None,
                    "error": next((err[d] for d in roots if err.get(d)), None),
                    "evidence": evidence_for(conn, tenant, frontier),
                })

            result["incidents"].sort(
                key=lambda i: (-i["devices_affected"], i["root_cause"]))

        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0

        print("=" * 62)
        print("  INCIDENTS")
        print("=" * 62)
        print(f"  vantage                 : {args.vantage}")
        print(f"  devices down            : {result['down_total']}")
        if result.get("error"):
            print(f"  CANNOT REASON           : {result['error']}")
        print(f"  incidents               : {len(result['incidents'])}")
        for inc in result["incidents"]:
            print()
            print(f"    ROOT CAUSE  {', '.join(inc['root_cause'])}")
            print(f"    since       {inc['started_at']}")
            if inc["error"]:
                print(f"    symptom     {inc['error']}")
            print(f"    affected    {inc['devices_affected']} devices, "
                  f"{inc['alerts_avoided']} alerts suppressed")
            if inc["suppressed"]:
                print(f"    suppressed  {', '.join(inc['suppressed'])}")
            for ev in inc["evidence"][:4]:
                print(f"    evidence    {ev['source']} from "
                      f"{ev['reporter'] or 'unknown'} at {ev['observed_at']}")
        if not result["incidents"] and not result.get("error"):
            print("  clean — nothing down inside the vantage's topology")
        if result["out_of_scope"]:
            print(f"  out of scope            : "
                  f"{', '.join(result['out_of_scope'])}")
            print("    (down, but not in this vantage's component — no root "
                  "cause can be claimed from here)")
        if result["unplaceable"]:
            print(f"  never discovered        : "
                  f"{', '.join(result['unplaceable'])}")
        print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
