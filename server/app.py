"""
Ingest and read API.

Runs on the central node. In an on-prem deployment that node belongs to
the customer; in a hybrid one it belongs to whoever operates the
service. The code is identical — what differs is who holds the keys.

Every request is scoped by the tenant derived from the collector key or
the read token. tenant_id is never accepted from a client.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from psycopg.rows import dict_row

from common.wire import WIRE_VERSION, device_from_json
from server import ingest

log = logging.getLogger("api")

app = FastAPI(title="Sovereign NMS", version="0.1")

_DSN = os.environ.get("DB_URL", "postgresql://nms:nms_dev_only@127.0.0.1:5432/nms")


def db():
    conn = psycopg.connect(_DSN, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Caller(BaseModel):
    key_id: str
    tenant_id: str
    site_id: str | None
    name: str


def authenticate(request: Request,
                 authorization: str = Header(default=""),
                 conn=Depends(db)) -> Caller:
    """
    Resolve the caller's tenant from their credential.

    This function is the entire tenant boundary. Everything downstream
    trusts the tenant_id it returns, so nothing here may read the
    request body — a caller must never be able to influence which
    tenant it writes to.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization[7:].strip()

    with conn.cursor() as cur:
        cur.execute(
            """SELECT key_id, tenant_id, site_id, name FROM collector_key
                WHERE key_hash = %s AND enabled""",
            (hash_key(token),))
        row = cur.fetchone()
        if not row:
            # Same message for unknown and disabled keys: distinguishing
            # them tells an attacker which tokens once existed.
            raise HTTPException(401, "invalid credential")

        cur.execute(
            """UPDATE collector_key
                  SET last_used_at = now(), last_used_ip = %s
                WHERE key_id = %s""",
            (request.client.host if request.client else None, row["key_id"]))
    conn.commit()

    return Caller(key_id=str(row["key_id"]), tenant_id=str(row["tenant_id"]),
                  site_id=str(row["site_id"]) if row["site_id"] else None,
                  name=row["name"])


# ------------------------------------------------------------------
# Ingest
# ------------------------------------------------------------------

@app.post("/v1/ingest/run")
def ingest_run(payload: dict[str, Any],
               caller: Caller = Depends(authenticate),
               conn=Depends(db)):
    """
    Accept one complete poll cycle.

    A whole run rather than per-device calls, because link building and
    FDB inference can only be done once every device in the cycle is
    known. Processing device-by-device would resolve almost nothing on
    the first pass and produce a topology that is briefly, visibly
    wrong.
    """
    version = payload.get("wire_version")
    if version != WIRE_VERSION:
        raise HTTPException(
            400, f"wire_version {version} not supported (expected {WIRE_VERSION})")

    devices = [device_from_json(d) for d in payload.get("devices", [])]
    if not devices:
        raise HTTPException(400, "run contains no devices")

    result = ingest.process_run(
        conn,
        tenant_id=caller.tenant_id,
        site_id=caller.site_id,
        collector_key_id=caller.key_id,
        collector_name=payload.get("collector_name") or caller.name,
        devices=devices,
    )
    conn.commit()
    return result


# ------------------------------------------------------------------
# Read
# ------------------------------------------------------------------

@app.get("/v1/devices")
def list_devices(caller: Caller = Depends(authenticate), conn=Depends(db)):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT d.device_id, d.display_name, d.vendor, d.model,
                      d.mgmt_ip, d.role, d.state::text AS state,
                      s.name AS site, d.last_seen,
                      (SELECT count(*) FROM interface i
                        WHERE i.device_id = d.device_id AND i.state='active') AS interfaces
                 FROM device d
            LEFT JOIN site s ON s.site_id = d.site_id
                WHERE d.tenant_id = %s
             ORDER BY d.display_name""",
            (caller.tenant_id,))
        return {"devices": cur.fetchall()}


@app.get("/v1/topology")
def topology(caller: Caller = Depends(authenticate), conn=Depends(db)):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT d.device_id, d.display_name, d.vendor, d.role,
                      d.state::text AS state, s.name AS site
                 FROM device d
            LEFT JOIN site s ON s.site_id = d.site_id
                WHERE d.tenant_id = %s AND d.state <> 'retired'""",
            (caller.tenant_id,))
        nodes = cur.fetchall()

        cur.execute(
            """SELECT l.link_id, l.device_a, l.device_b,
                      ia.if_name AS port_a, ib.if_name AS port_b,
                      l.fidelity::text AS fidelity, l.confidence,
                      l.state::text AS state,
                      (SELECT array_agg(DISTINCT e.source::text)
                         FROM link_evidence e WHERE e.link_id = l.link_id) AS sources
                 FROM link l
            LEFT JOIN interface ia ON ia.interface_id = l.endpoint_a
            LEFT JOIN interface ib ON ib.interface_id = l.endpoint_b
                WHERE l.tenant_id = %s AND l.state = 'active'""",
            (caller.tenant_id,))
        edges = cur.fetchall()

    return {"nodes": nodes, "edges": edges}


@app.get("/v1/runs")
def runs(limit: int = 20, caller: Caller = Depends(authenticate), conn=Depends(db)):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.run_id, r.started_at, r.finished_at, r.devices_seen,
                      r.links_asserted, r.links_retracted, r.auto_edge_pct,
                      c.name AS collector, s.name AS site
                 FROM discovery_run r
            LEFT JOIN collector_key c ON c.key_id = r.collector_key_id
            LEFT JOIN site s ON s.site_id = r.site_id
                WHERE r.tenant_id = %s
             ORDER BY r.started_at DESC LIMIT %s""",
            (caller.tenant_id, min(limit, 100)))
        return {"runs": cur.fetchall()}


@app.get("/v1/health")
def health(caller: Caller = Depends(authenticate), conn=Depends(db)):
    """
    Data-quality checks, not liveness.

    cross_site_links is the one that matters in a multi-tenant or
    multi-site deployment: a link spanning two sites means an identity
    collision, most likely two networks using the same address space.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM link_cross_site WHERE tenant_id = %s",
                    (caller.tenant_id,))
        cross = cur.fetchone()["n"]

        cur.execute(
            """SELECT count(*) AS n FROM device
                WHERE tenant_id = %s AND state = 'unpolled'""",
            (caller.tenant_id,))
        unpolled = cur.fetchone()["n"]

        cur.execute(
            """SELECT count(*) AS n FROM device
                WHERE tenant_id = %s AND site_id IS NULL""",
            (caller.tenant_id,))
        siteless = cur.fetchone()["n"]

        cur.execute(
            """SELECT max(finished_at) AS last FROM discovery_run
                WHERE tenant_id = %s""",
            (caller.tenant_id,))
        last = cur.fetchone()["last"]

    stale_minutes = None
    if last:
        stale_minutes = round(
            (datetime.now(timezone.utc) - last).total_seconds() / 60, 1)

    return {
        "cross_site_links": cross,
        "unpolled_devices": unpolled,
        "devices_without_site": siteless,
        "last_run_finished": last,
        "minutes_since_last_run": stale_minutes,
    }


app.mount("/ui", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web", html=True), name="ui")
