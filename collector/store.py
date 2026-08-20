"""
Identity resolution and persistence.

The core idea: a poll produces a set of CLAIMS about identity
(sysName says X, chassis says Y, we reached it at IP Z). Each claim is
looked up in device_identity. If any claim already points at a device,
that is the device — and the remaining claims are attached to it.

This is why a renumbered device is still the same device: the IP claim
misses, but the sysName claim hits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

# Ordered strongest first. A stable hardware identifier outranks a
# mutable one.
IDENTITY_PRECEDENCE = ("serial", "chassis_id", "base_mac", "sysname", "mgmt_ip")

# Only these may ESTABLISH that two observations are the same device.
#
# mgmt_ip is deliberately excluded. It is recorded (useful for search,
# for reachability, as corroboration) but it can never resolve identity
# on its own: addresses are reassigned, reused across sites, and shared
# by every device behind one NAT or simulator. Letting a weak identifier
# resolve means two unrelated devices silently become one — and the
# merge is invisible because it produces no error, just wrong data.
RESOLVING_TYPES = frozenset({"serial", "chassis_id", "base_mac", "sysname"})

# sysName values that identify a product line, not a device. Using one
# as an identity key merges every unconfigured unit of that model.
GENERIC_SYSNAMES = {
    "mikrotik", "switch", "router", "ap", "accesspoint",
    "localhost", "unknown", "default", "openwrt", "raspberrypi",
}


@dataclass(frozen=True)
class IdentityClaim:
    id_type: str
    id_value: str
    source: str = "inferred"
    confidence: float = 1.0


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)


def resolve_device(conn, tenant_id: str, claims: list[IdentityClaim],
                   display_name: str, mgmt_ip: str | None,
                   vendor: str | None = None,
                   model: str | None = None,
                   os_version: str | None = None) -> tuple[str, bool]:
    """
    Return (device_id, created). Never raises on ambiguity — logs and
    picks the strongest claim, because a collector that halts on an
    ambiguous device stops collecting the other 200.
    """
    claims = [c for c in claims if c.id_value]
    claims = [c for c in claims
              if not (c.id_type == "sysname"
                      and c.id_value.strip().lower() in GENERIC_SYSNAMES)]
    if not any(c.id_type in RESOLVING_TYPES for c in claims):
        # Refuse rather than create an unidentifiable device. A device
        # with only a mgmt_ip cannot be tracked across a renumbering and
        # will silently absorb the next device at that address.
        raise ValueError(
            f"{display_name}: no resolving identity claim "
            f"(have: {[c.id_type for c in claims] or 'none'})")

    order = {t: i for i, t in enumerate(IDENTITY_PRECEDENCE)}
    claims.sort(key=lambda c: order.get(c.id_type, 99))

    with conn.cursor() as cur:
        # Which existing devices do these claims point at? Only resolving
        # identifiers are consulted — a shared mgmt_ip must not merge two
        # devices that have nothing else in common.
        matched: dict[str, IdentityClaim] = {}
        for c in claims:
            if c.id_type not in RESOLVING_TYPES:
                continue
            cur.execute(
                """SELECT device_id FROM device_identity
                   WHERE tenant_id = %s AND id_type = %s AND id_value = %s
                     AND device_id IS NOT NULL""",
                (tenant_id, c.id_type, c.id_value))
            for row in cur.fetchall():
                matched.setdefault(str(row["device_id"]), c)

        if len(matched) > 1:
            # Two identities we thought were separate devices are in fact
            # one, or an identifier was reused. Do NOT auto-merge — that
            # destroys history. Take the strongest and flag it.
            log.warning(
                "%s: identity claims match %d devices %s — using strongest (%s=%s). "
                "Manual merge required.",
                display_name, len(matched), list(matched), claims[0].id_type,
                claims[0].id_value)

        if matched:
            device_id = min(
                matched,
                key=lambda d: order.get(matched[d].id_type, 99))
            created = False
            cur.execute(
                """UPDATE device
                      SET display_name = %s,
                          mgmt_ip = COALESCE(%s::inet, mgmt_ip),
                          vendor = COALESCE(%s, vendor),
                          model = COALESCE(%s, model),
                          os_version = COALESCE(%s, os_version),
                          state = 'active',
                          last_seen = now()
                    WHERE device_id = %s""",
                (display_name, mgmt_ip, vendor, model, os_version, device_id))
        else:
            cur.execute(
                """INSERT INTO device
                       (tenant_id, display_name, mgmt_ip, vendor, model, os_version)
                   VALUES (%s, %s, %s::inet, %s, %s, %s)
                   RETURNING device_id""",
                (tenant_id, display_name, mgmt_ip, vendor, model, os_version))
            device_id = str(cur.fetchone()["device_id"])
            created = True

        # Attach every claim, including ones that missed. Next poll they
        # become hits — that is how the graph self-corrects.
        for c in claims:
            cur.execute(
                """SELECT device_id FROM device_identity
                    WHERE tenant_id = %s AND id_type = %s
                      AND id_value = %s AND source = %s::source_kind""",
                (tenant_id, c.id_type, c.id_value, c.source))
            prior = cur.fetchone()
            if prior and prior["device_id"] and str(prior["device_id"]) != device_id:
                # The same identifier now describes a different device.
                # Expected for mgmt_ip (DHCP, NAT, reuse). Alarming for a
                # burned-in MAC — that means a NIC moved or the value is
                # not as unique as assumed.
                level = log.info if c.id_type == "mgmt_ip" else log.warning
                level("%s: %s=%s reassigned from device %s to %s",
                      display_name, c.id_type, c.id_value,
                      str(prior["device_id"])[:8], device_id[:8])

            cur.execute(
                """INSERT INTO device_identity
                       (tenant_id, device_id, id_type, id_value, source, confidence)
                   VALUES (%s, %s, %s, %s, %s::source_kind, %s)
                   ON CONFLICT (tenant_id, id_type, id_value, source)
                   DO UPDATE SET device_id  = EXCLUDED.device_id,
                                 last_seen  = now(),
                                 confidence = EXCLUDED.confidence""",
                (tenant_id, device_id, c.id_type, c.id_value, c.source, c.confidence))

    return device_id, created


def upsert_interfaces(conn, tenant_id: str, device_id: str, ifaces) -> tuple[int, int]:
    """
    Upsert on (device_id, if_name). Returns (seen, retired).

    Interfaces absent from this poll are marked stale, not deleted —
    a device that drops an interface for one cycle should not lose its
    history.
    """
    seen_names: list[str] = []
    with conn.cursor() as cur:
        for iface in ifaces.values():
            name = iface.key_name
            seen_names.append(name)
            cur.execute(
                """INSERT INTO interface
                       (tenant_id, device_id, if_index, if_name, if_alias,
                        mac_address, speed_bps, if_type, admin_status, oper_status)
                   VALUES (%s, %s, %s, %s, %s, %s::macaddr, %s, %s, %s, %s)
                   ON CONFLICT (device_id, if_name) DO UPDATE SET
                        if_index     = EXCLUDED.if_index,
                        if_alias     = EXCLUDED.if_alias,
                        mac_address  = COALESCE(EXCLUDED.mac_address, interface.mac_address),
                        speed_bps    = COALESCE(EXCLUDED.speed_bps, interface.speed_bps),
                        if_type      = EXCLUDED.if_type,
                        admin_status = EXCLUDED.admin_status,
                        oper_status  = EXCLUDED.oper_status,
                        state        = 'active',
                        last_seen    = now()""",
                (tenant_id, device_id, iface.if_index, name, iface.if_alias,
                 iface.mac_address, iface.speed_bps, iface.if_type,
                 iface.admin_status, iface.oper_status))

        retired = 0
        if seen_names:
            cur.execute(
                """UPDATE interface SET state = 'stale'
                    WHERE device_id = %s AND state = 'active'
                      AND NOT (if_name = ANY(%s))""",
                (device_id, seen_names))
            retired = cur.rowcount
    return len(seen_names), retired


def write_metrics(conn, tenant_id: str, device_id: str, ifaces, if_ids: dict[str, str]):
    rows = []
    for iface in ifaces.values():
        iface_id = if_ids.get(iface.key_name)
        if not iface_id:
            continue
        for metric, value in iface.counters.items():
            rows.append((tenant_id, "interface", iface_id, metric, float(value), "octets"))
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO metric_sample
                   (tenant_id, entity_type, entity_id, metric_name, ts, value, unit, source)
               VALUES (%s, %s, %s, %s, now(), %s, %s, 'inferred'::source_kind)""",
            rows)
    return len(rows)


def interface_ids(conn, device_id: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT if_name, interface_id FROM interface WHERE device_id = %s",
            (device_id,))
        return {r["if_name"]: str(r["interface_id"]) for r in cur.fetchall()}


def start_run(conn, tenant_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO discovery_run (tenant_id) VALUES (%s) RETURNING run_id",
            (tenant_id,))
        return str(cur.fetchone()["run_id"])


def finish_run(conn, run_id: str, devices_seen: int):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE discovery_run
                  SET finished_at = now(), devices_seen = %s
                WHERE run_id = %s""",
            (devices_seen, run_id))
