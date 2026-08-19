#!/usr/bin/env python3
"""
Issue a collector credential.

The plaintext token is printed once and never stored — only its
SHA-256. A stolen database yields no working keys.

    python scripts/create_collector_key.py --tenant lab --site hq --name optiplex-01
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def load_env(path: Path) -> dict[str, str]:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="tenant name")
    ap.add_argument("--site", help="site name (created if absent)")
    ap.add_argument("--name", required=True, help="collector name")
    ap.add_argument("--env", default=".env")
    args = ap.parse_args()

    env = load_env(Path(args.env))
    dsn = os.environ.get("DB_URL") or env.get("DB_URL")
    if not dsn:
        print("DB_URL required", file=sys.stderr)
        return 1

    token = "nmsk_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(token.encode()).hexdigest()

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM tenant WHERE name = %s", (args.tenant,))
            row = cur.fetchone()
            if not row:
                cur.execute("INSERT INTO tenant (name) VALUES (%s) RETURNING tenant_id",
                            (args.tenant,))
                row = cur.fetchone()
                print(f"created tenant {args.tenant}")
            tenant_id = row["tenant_id"]

            site_id = None
            if args.site:
                cur.execute("SELECT site_id FROM site WHERE tenant_id=%s AND name=%s",
                            (tenant_id, args.site))
                s = cur.fetchone()
                if not s:
                    cur.execute(
                        "INSERT INTO site (tenant_id, name) VALUES (%s,%s) RETURNING site_id",
                        (tenant_id, args.site))
                    s = cur.fetchone()
                    print(f"created site {args.site}")
                site_id = s["site_id"]

            cur.execute(
                """INSERT INTO collector_key (tenant_id, site_id, name, key_hash)
                   VALUES (%s,%s,%s,%s) RETURNING key_id""",
                (tenant_id, site_id, args.name, key_hash))
            key_id = cur.fetchone()["key_id"]
        conn.commit()

    print()
    print("=" * 60)
    print(f"  collector : {args.name}")
    print(f"  tenant    : {args.tenant}")
    print(f"  site      : {args.site or '(none)'}")
    print(f"  key_id    : {key_id}")
    print()
    print(f"  TOKEN     : {token}")
    print()
    print("  Shown once. Store it in the collector's .env as")
    print("  INGEST_TOKEN. It cannot be recovered.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
