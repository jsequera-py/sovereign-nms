"""
Turn LLDP neighbour observations into links.

The pipeline per neighbour:
  1. resolve the neighbour to a device (creating an 'unpolled'
     placeholder if it has never been seen directly)
  2. decide fidelity: interface-level if BOTH ends resolve to a known
     interface, device-level otherwise
  3. upsert the link, canonically ordered by device id
  4. record evidence keyed on the reporting device
  5. recompute confidence from fresh, independent evidence

Confidence is NEVER written directly. It is always a function of the
evidence rows. If you find code setting link.confidence by hand, that
is a bug.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# How much a single source is worth on its own. These are priors, not
# measurements — tune them against the scorer, not against intuition.
SOURCE_BASE = {
    "manual": 1.00,
    "lldp": 0.65,
    "cdp": 0.60,
    "api": 0.60,
    "mac_table": 0.40,
    "arp": 0.30,
    "route": 0.25,
    "traceroute": 0.25,
    "inferred": 0.20,
}

# Each additional INDEPENDENT confirmation adds this much.
AGREEMENT_BONUS = 0.15
# Nothing automated reaches certainty. Only a human assertion does.
AUTO_CEILING = 0.95

GENERIC_SYSNAMES = {
    "mikrotik", "switch", "router", "ap", "accesspoint",
    "localhost", "unknown", "default", "openwrt", "raspberrypi",
}


@dataclass
class NeighborObservation:
    """One adjacency as reported by one device."""
    reporter_device_id: str
    reporter_interface_id: str | None
    reporter_if_name: str | None
    peer_chassis_mac: str | None
    peer_sysname: str | None
    peer_if_name: str | None
    peer_sys_desc: str | None
    source: str = "lldp"


def resolve_peer_device(conn, tenant_id: str, obs: NeighborObservation) -> tuple[str | None, bool]:
    """
    Find (or create) the device this neighbour refers to.

    Returns (device_id, created_placeholder).

    Chassis MAC is the strongest signal and is tried first. sysName is
    a fallback and is refused when it is a factory default — 'MikroTik'
    identifies a product line, and matching on it would merge every
    unconfigured unit of that model into one device.
    """
    candidates: list[tuple[str, str]] = []
    if obs.peer_chassis_mac:
        candidates.append(("chassis_id", obs.peer_chassis_mac))
        # Some vendors use a port MAC as the chassis id. Try that too,
        # but only after the dedicated chassis_id lookup misses.
        candidates.append(("base_mac", obs.peer_chassis_mac))
    if obs.peer_sysname and obs.peer_sysname.strip().lower() not in GENERIC_SYSNAMES:
        candidates.append(("sysname", obs.peer_sysname))

    if not candidates:
        return None, False

    with conn.cursor() as cur:
        for id_type, id_value in candidates:
            cur.execute(
                """SELECT device_id FROM device_identity
                    WHERE tenant_id = %s AND id_type = %s AND id_value = %s
                      AND device_id IS NOT NULL LIMIT 1""",
                (tenant_id, id_type, id_value))
            row = cur.fetchone()
            if row:
                return str(row["device_id"]), False

        # Never seen directly. Create a placeholder so the adjacency can
        # be recorded at all — an eero mesh AP advertises LLDP and
        # serves no SNMP; it is real, adjacent, and unpollable.
        #
        # state='unpolled' keeps it out of monitored counts and out of
        # alerting. A device we have never spoken to must not be able to
        # raise an incident.
        display = obs.peer_sysname or obs.peer_chassis_mac or "unknown-neighbour"
        cur.execute(
            """INSERT INTO device (tenant_id, display_name, os_version, state)
               VALUES (%s, %s, %s, 'unpolled') RETURNING device_id""",
            (tenant_id, display, obs.peer_sys_desc))
        device_id = str(cur.fetchone()["device_id"])

        for id_type, id_value in candidates:
            if id_type == "base_mac":
                continue     # do not assert a guess as an identity
            cur.execute(
                """INSERT INTO device_identity
                       (tenant_id, device_id, id_type, id_value, source, confidence)
                   VALUES (%s, %s, %s, %s, 'lldp'::source_kind, 0.8)
                   ON CONFLICT (tenant_id, id_type, id_value, source)
                   DO UPDATE SET device_id = EXCLUDED.device_id, last_seen = now()""",
                (tenant_id, device_id, id_type, id_value))

    log.info("created unpolled device %s from %s advertisement",
             display, obs.source)
    return device_id, True


def find_interface(conn, device_id: str, if_name: str | None) -> str | None:
    if not if_name:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT interface_id FROM interface WHERE device_id = %s AND if_name = %s",
            (device_id, if_name))
        row = cur.fetchone()
        return str(row["interface_id"]) if row else None


def upsert_link(conn, tenant_id: str, dev_a: str, dev_b: str,
                if_a: str | None, if_b: str | None) -> tuple[str, str]:
    """
    Insert or find the link. Returns (link_id, fidelity).

    Ordering is canonical on device id so the same cable reported from
    either end lands on one row.
    """
    interface_level = bool(if_a and if_b)
    # Order the pair, keeping endpoints attached to their own device.
    if dev_a > dev_b:
        dev_a, dev_b = dev_b, dev_a
        if_a, if_b = if_b, if_a
    fidelity = "interface" if interface_level else "device"
    if not interface_level:
        if_a = if_b = None

    with conn.cursor() as cur:
        # An interface-level observation supersedes a device-level row
        # for the same pair: same physical fact, better information.
        # Updating in place preserves link_id and its evidence history.
        if interface_level:
            cur.execute(
                """SELECT link_id FROM link
                    WHERE tenant_id = %s AND device_a = %s AND device_b = %s
                      AND endpoint_a IS NULL AND endpoint_b IS NULL""",
                (tenant_id, dev_a, dev_b))
            row = cur.fetchone()
            if row:
                cur.execute(
                    """UPDATE link
                          SET endpoint_a = %s, endpoint_b = %s,
                              fidelity = 'interface', state = 'active',
                              last_seen = now()
                        WHERE link_id = %s""",
                    (if_a, if_b, row["link_id"]))
                return str(row["link_id"]), fidelity
        else:
            # The mirror case, and the one that bites.
            #
            # A device-level observation for a pair that ALREADY has an
            # interface-level link is the same cable seen with less
            # detail — a MikroTik confirming an adjacency its Cisco
            # neighbour already described precisely. Inserting a
            # device-level row here does not conflict (NULL endpoints
            # form a different tuple), so it silently creates a SECOND
            # link for one cable, double-counting the topology.
            #
            # Attach the evidence to the existing link instead.
            cur.execute(
                """SELECT link_id FROM link
                    WHERE tenant_id = %s AND device_a = %s AND device_b = %s
                      AND fidelity = 'interface' AND state = 'active'""",
                (tenant_id, dev_a, dev_b))
            existing = cur.fetchall()
            if len(existing) == 1:
                cur.execute(
                    "UPDATE link SET state = 'active', last_seen = now() WHERE link_id = %s",
                    (existing[0]["link_id"],))
                return str(existing[0]["link_id"]), "interface"
            # Two or more cables between the same pair (LAG, redundant
            # uplinks): a portless observation cannot say which one it
            # confirms. Keep it as its own device-level row rather than
            # attributing it arbitrarily.

        cur.execute(
            """INSERT INTO link
                   (tenant_id, device_a, device_b, endpoint_a, endpoint_b, fidelity)
               VALUES (%s, %s, %s, %s, %s, %s::link_fidelity)
               ON CONFLICT (device_a, device_b, endpoint_a, endpoint_b)
               DO UPDATE SET state = 'active', last_seen = now()
               RETURNING link_id""",
            (tenant_id, dev_a, dev_b, if_a, if_b, fidelity))
        return str(cur.fetchone()["link_id"]), fidelity


def record_evidence(conn, link_id: str, obs: NeighborObservation) -> None:
    """
    One row per (link, source, reporter). Re-polling refreshes it
    rather than adding a duplicate, so the row count stays equal to the
    number of independent confirmations.
    """
    claim = json.dumps({
        "reporter_if": obs.reporter_if_name,
        "peer_sysname": obs.peer_sysname,
        "peer_if": obs.peer_if_name,
        "peer_chassis": obs.peer_chassis_mac,
    })
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO link_evidence
                   (link_id, source, reporter_device_id, reporter_interface_id,
                    raw_claim, asserts, observed_at)
               VALUES (%s, %s::source_kind, %s, %s, %s::jsonb, TRUE, now())
               ON CONFLICT (link_id, source, reporter_device_id)
               DO UPDATE SET observed_at = now(),
                             raw_claim = EXCLUDED.raw_claim,
                             reporter_interface_id = EXCLUDED.reporter_interface_id,
                             asserts = TRUE""",
            (link_id, obs.source, obs.reporter_device_id,
             obs.reporter_interface_id, claim))


def rollup_confidence(conn, tenant_id: str) -> dict[str, int]:
    """
    Recompute every link's confidence from fresh evidence.

    Independence is counted per (source, reporter). Both ends of a cable
    reporting via LLDP is two independent confirmations. One end polled
    five times is one.
    """
    stats = {"scored": 0, "stale": 0}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT l.link_id,
                      COALESCE(array_agg(DISTINCT e.source::text)
                               FILTER (WHERE e.link_id IS NOT NULL), '{}') AS sources,
                      count(DISTINCT (e.source, e.reporter_device_id))
                               FILTER (WHERE e.link_id IS NOT NULL) AS confirmations
                 FROM link l
            LEFT JOIN link_evidence_fresh e ON e.link_id = l.link_id
                WHERE l.tenant_id = %s AND l.pinned = FALSE
             GROUP BY l.link_id""",
            (tenant_id,))
        rows = cur.fetchall()

        for row in rows:
            sources = row["sources"] or []
            confirmations = int(row["confirmations"] or 0)

            if confirmations == 0:
                # Nothing fresh asserts this link any more. Do not delete
                # it — mark it stale so the retraction is visible and
                # reversible.
                cur.execute(
                    "UPDATE link SET confidence = 0, state = 'stale' WHERE link_id = %s",
                    (row["link_id"],))
                stats["stale"] += 1
                continue

            base = max(SOURCE_BASE.get(s, 0.2) for s in sources)
            score = base + AGREEMENT_BONUS * (confirmations - 1)
            score = min(score, 1.0 if "manual" in sources else AUTO_CEILING)

            cur.execute(
                "UPDATE link SET confidence = %s, state = 'active' WHERE link_id = %s",
                (round(score, 3), row["link_id"]))
            stats["scored"] += 1

    return stats


def build_links(conn, tenant_id: str, reporter_device_id: str,
                view, source: str = "lldp") -> dict[str, int]:
    """Process one device's LLDP view into links and evidence."""
    counts = {"links": 0, "interface": 0, "device": 0,
              "placeholders": 0, "unidentifiable": 0}

    for nbr in view.neighbors:
        if not nbr.is_identifiable():
            # Chassis subtype we cannot interpret and no usable name.
            # Recording it would create a device we can never match
            # again — a permanent ghost in the topology.
            counts["unidentifiable"] += 1
            continue

        local_if_name = view.local_interface_for(nbr)
        obs = NeighborObservation(
            reporter_device_id=reporter_device_id,
            reporter_interface_id=find_interface(conn, reporter_device_id, local_if_name),
            reporter_if_name=local_if_name,
            peer_chassis_mac=nbr.chassis_mac,
            peer_sysname=nbr.usable_sysname,
            peer_if_name=nbr.remote_interface_name,
            peer_sys_desc=nbr.sys_desc,
            source=source,
        )

        peer_id, created = resolve_peer_device(conn, tenant_id, obs)
        if not peer_id:
            counts["unidentifiable"] += 1
            continue
        if created:
            counts["placeholders"] += 1
        if peer_id == reporter_device_id:
            # A device reporting itself. Happens with reflected LLDP on
            # a hub or a mirrored port; a self-loop is never useful.
            continue

        peer_if_id = find_interface(conn, peer_id, obs.peer_if_name)

        link_id, fidelity = upsert_link(
            conn, tenant_id, reporter_device_id, peer_id,
            obs.reporter_interface_id, peer_if_id)
        record_evidence(conn, link_id, obs)

        counts["links"] += 1
        counts[fidelity] += 1

    return counts
