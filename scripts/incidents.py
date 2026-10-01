"""
Incidents CLI: print the incident report from server/incidents.py.

The engine and its four rules live in server/incidents.py, shared with
GET /v1/incidents. This file only loads .env, connects, and prints.

Run:
    .venv/bin/python scripts/incidents.py --vantage srv-mon-01
    .venv/bin/python scripts/incidents.py --vantage srv-mon-01 --json

Exit 0 always. This reports; it does not grade. Grading is
scripts/check_incidents.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.incidents import compute  # noqa: E402


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
        result = compute(conn, tenant, args.vantage)

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
