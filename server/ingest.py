"""
Server-side processing of a poll run.

This is where everything that needs the whole database happens:
identity resolution, link building, FDB inference, confidence rollup.
The collector does none of it, because none of it can be done correctly
from inside a single site.

Order matters and is not arbitrary:
  1. all devices and interfaces      (nothing can be resolved before this)
  2. LLDP links                      (needs interfaces on both ends)
  3. FDB inference                   (needs MAC ownership across the fleet)
  4. confidence rollup               (needs all evidence present)

Doing 2 during 1 produces device-level links that only upgrade on the
next cycle — correct eventually, briefly wrong in a way that is hard to
explain to someone watching a screen.
"""
from __future__ import annotations

import logging
from collections import namedtuple

sys_path_guard = None

from collector import fdb as fdb_mod
from collector import store, topology
from common.wire import DeviceObs

log = logging.getLogger("ingest")

# Duck-typed stand-ins so the existing collector code can consume wire
# objects without change.
_Iface = namedtuple(
    "_Iface",
    "if_index if_name if_alias if_type mac_address speed_bps "
    "admin_status oper_status counters key_name")


def _iface_view(obs) -> dict[int, object]:
    out = {}
    for i in obs.interfaces:
        if store.is_ignored_ifname(i.if_name):
            continue
        out[i.if_index] = _Iface(
            if_index=i.if_index, if_name=i.if_name, if_alias=i.if_alias,
            if_type=i.if_type, mac_address=i.mac_address,
            speed_bps=i.speed_bps, admin_status=i.admin_status,
            oper_status=i.oper_status, counters=i.counters or {},
            key_name=i.if_name)
    return out


def _claims(obs: DeviceObs) -> list[store.IdentityClaim]:
    from collector.mib import is_hardware_mac

    claims: list[store.IdentityClaim] = []
    if obs.sys_name:
        claims.append(store.IdentityClaim("sysname", obs.sys_name, "inferred"))
    if obs.lldp_local_chassis_mac:
        claims.append(store.IdentityClaim(
            "chassis_id", obs.lldp_local_chassis_mac, "lldp"))
    for mac in sorted({i.mac_address for i in obs.interfaces
                       if i.mac_address and is_hardware_mac(i.mac_address)
                       and i.if_type != "loopback"}):
        claims.append(store.IdentityClaim("base_mac", mac, "inferred"))
    if obs.mgmt_ip:
        claims.append(store.IdentityClaim("mgmt_ip", obs.mgmt_ip, "inferred", 0.5))
    return claims


class _LldpView:
    """Adapts wire neighbours to what topology.build_links expects."""

    def __init__(self, obs: DeviceObs):
        self._obs = obs
        self.neighbors = [_Nbr(n) for n in obs.neighbors]

    def local_interface_for(self, nbr) -> str | None:
        return nbr.local_if_name


class _Nbr:
    def __init__(self, n):
        self.local_if_name = n.local_if_name
        self.chassis_mac = n.chassis_mac
        self.usable_sysname = (
            n.sys_name
            if n.sys_name and n.sys_name.strip().lower() not in store.GENERIC_SYSNAMES
            else None)
        self.remote_interface_name = n.peer_if_name
        self.sys_desc = n.sys_desc

    def is_identifiable(self) -> bool:
        return bool(self.chassis_mac or self.usable_sysname)


def process_run(conn, tenant_id: str, site_id: str | None,
                collector_key_id: str, collector_name: str,
                devices: list[DeviceObs]) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO discovery_run
                   (tenant_id, site_id, collector_key_id)
               VALUES (%s, %s, %s) RETURNING run_id""",
            (tenant_id, site_id, collector_key_id))
        run_id = str(cur.fetchone()["run_id"])

    device_ids: dict[str, str] = {}
    interfaces_total = 0
    unreachable = 0

    # --- 1. devices and interfaces ------------------------------------
    for obs in devices:
        if not obs.reachable:
            store.record_reachability(conn, tenant_id, obs.poll_target, None,
                                      obs.reach_status, obs.error)
            unreachable += 1
            continue
        claims = _claims(obs)
        if not any(c.id_type in store.RESOLVING_TYPES for c in claims):
            store.record_reachability(conn, tenant_id, obs.poll_target, None,
                                      obs.reach_status, obs.error)
            log.warning("%s: no resolving identity, skipped", obs.poll_target)
            continue

        device_id, _ = store.resolve_device(
            conn, tenant_id, claims,
            display_name=obs.sys_name or obs.poll_target,
            mgmt_ip=obs.mgmt_ip, vendor=obs.vendor, os_version=obs.sys_descr)
        store.record_reachability(conn, tenant_id, obs.poll_target, device_id,
                                  obs.reach_status, obs.error)

        # Site comes from the collector's credential, not the payload.
        with conn.cursor() as cur:
            cur.execute("UPDATE device SET site_id = %s WHERE device_id = %s",
                        (site_id, device_id))

        ifaces = _iface_view(obs)
        seen, _ = store.upsert_interfaces(conn, tenant_id, device_id, ifaces)
        if_ids = store.interface_ids(conn, device_id)
        store.write_metrics(conn, tenant_id, device_id, ifaces, if_ids)

        device_ids[obs.poll_target] = device_id
        interfaces_total += seen

    # --- 2. LLDP links -------------------------------------------------
    link_counts = {"links": 0, "interface": 0, "device": 0, "placeholders": 0}
    for obs in devices:
        device_id = device_ids.get(obs.poll_target)
        if not device_id or not obs.neighbors:
            continue
        counts = topology.build_links(conn, tenant_id, device_id, _LldpView(obs))
        for k in link_counts:
            link_counts[k] += counts.get(k, 0)

    # --- 3. FDB inference ---------------------------------------------
    owners = topology.mac_ownership(conn, tenant_id)
    inferred = 0
    for obs in devices:
        device_id = device_ids.get(obs.poll_target)
        if not device_id or not obs.fdb or not obs.fdb.ports:
            continue
        view = fdb_mod.FdbView(
            port_ifindex=obs.fdb.port_ifindex,
            entries=[fdb_mod.FdbEntry(mac=m, bridge_port=p, status="3")
                     for p, macs in obs.fdb.ports.items() for m in macs])
        candidates = fdb_mod.find_leaf_ports(view, device_id, owners)
        if candidates:
            c = topology.build_links_from_fdb(conn, tenant_id, device_id, candidates)
            inferred += c["links"]

    # --- 4. confidence -------------------------------------------------
    roll = topology.rollup_confidence(conn, tenant_id)

    # auto_edge_pct: the share of links discovered without a human
    # drawing them. This is the differentiator expressed as a number,
    # and it belongs on every run so the trend is visible.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FILTER (WHERE NOT pinned) AS auto,
                      count(*) AS total
                 FROM link WHERE tenant_id = %s AND state = 'active'""",
            (tenant_id,))
        r = cur.fetchone()
        auto_pct = (r["auto"] / r["total"] * 100.0) if r["total"] else None

        cur.execute(
            """UPDATE discovery_run
                  SET finished_at = now(), devices_seen = %s,
                      links_asserted = %s, links_retracted = %s,
                      auto_edge_pct = %s
                WHERE run_id = %s""",
            (len(device_ids), roll["scored"], roll["stale"], auto_pct, run_id))

    return {
        "run_id": run_id,
        "devices_ingested": len(device_ids),
        "devices_unreachable": unreachable,
        "interfaces": interfaces_total,
        "lldp_links": link_counts["links"],
        "fdb_inferred_links": inferred,
        "placeholders_created": link_counts["placeholders"],
        "links_scored": roll["scored"],
        "links_stale": roll["stale"],
        "auto_edge_pct": round(auto_pct, 1) if auto_pct is not None else None,
    }
