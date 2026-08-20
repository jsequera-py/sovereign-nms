#!/usr/bin/env python3
"""
Poll every device in inventory.yaml, resolve identity, persist.

Run:
    python -m collector.poll                 # live poll
    python -m collector.poll --dry-run       # parse only, no DB writes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import namedtuple
from pathlib import Path

import yaml

from common.wire import DeviceObs, InterfaceObs, NeighborObs
from . import fdb, lldp, mib, store, topology
from .snmp import SnmpEmpty, SnmpError, SnmpTarget, walk

log = logging.getLogger("collector")


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def build_targets(cfg: dict) -> list[tuple[SnmpTarget, str | None]]:
    out = []
    for d in cfg.get("devices", []):
        t = SnmpTarget(
            name=d["name"],
            host=d.get("host", "127.0.0.1"),
            port=int(d.get("port", 161)),
            version=str(d.get("version", "2c")),
            community=d.get("community"),
            sec_name=d.get("sec_name"),
            sec_level=d.get("sec_level", "authPriv"),
            auth_proto=d.get("auth_proto"),
            auth_pass=d.get("auth_pass"),
            priv_proto=d.get("priv_proto"),
            priv_pass=d.get("priv_pass"),
            timeout_s=int(d.get("timeout_s", 3)),
            retries=int(d.get("retries", 1)),
        )
        out.append((t, d.get("recorded")))
    return out


def claims_for(sys_name: str | None, ifaces, target: SnmpTarget,
               recorded: str | None) -> list[store.IdentityClaim]:
    """
    Every identifier this poll observed. Weak ones are included on
    purpose — an identifier that misses today becomes the anchor that
    survives a renumbering tomorrow.
    """
    claims: list[store.IdentityClaim] = []
    if sys_name:
        claims.append(store.IdentityClaim("sysname", sys_name, "inferred"))

    # EVERY burned-in MAC becomes a claim, not just one. If a NIC is
    # removed or an address changes, the remaining claims still resolve
    # the device. Software-generated MACs (docker0, br-*, veth*) are
    # excluded — they regenerate and would poison identity.
    macs = sorted({
        i.mac_address for i in ifaces.values()
        if mib.is_hardware_mac(i.mac_address) and i.if_type != "loopback"})
    for mac in macs:
        claims.append(store.IdentityClaim("base_mac", mac, "inferred"))

    # A replayed walk has no meaningful management address.
    if not recorded:
        claims.append(store.IdentityClaim("mgmt_ip", target.host, "inferred", 0.5))
    return claims


def lldp_claims(local_chassis_mac: str | None) -> list[store.IdentityClaim]:
    """
    Identity a device asserts about itself over LLDP.

    chassis_id is its own id_type rather than being folded into
    base_mac: the chassis identifier is not required to equal any
    interface MAC, and on some firmware it is absent entirely.
    Conflating them would make neighbour resolution depend on a vendor
    convention rather than on what the device actually said.
    """
    claims = []
    if local_chassis_mac:
        claims.append(store.IdentityClaim("chassis_id", local_chassis_mac, "lldp"))
    return claims


def observe_one(target: SnmpTarget, recorded: str | None) -> DeviceObs:
    """
    Pure function of the wire: walk SNMP, parse, report what was seen.

    No connection, no tenant_id, no DB — this is the single observation
    that feeds either persistence path, direct or over the API.
    """
    try:
        sys_binds = walk(target, mib.SYSTEM_ROOT, recorded)
    except SnmpEmpty:
        log.error("%s: reachable but system MIB is empty — SNMP view is "
                  "restricted, not a failure", target.name)
        return DeviceObs(poll_target=target.name, reachable=False,
                         error="SNMP view restricted", reach_status="filtered")
    except SnmpError as exc:
        log.error("%s: unreachable — %s", target.name, exc)
        return DeviceObs(poll_target=target.name, reachable=False,
                         error=str(exc), reach_status="unreachable")

    sysinfo = mib.parse_system(sys_binds)

    if_binds = []
    for root in (mib.IF_MIB_ROOT, mib.IF_XMIB_ROOT):
        try:
            if_binds += walk(target, root, recorded)
        except SnmpEmpty:
            log.warning("%s: no rows under %s", target.name, root)
        except SnmpError as exc:
            log.warning("%s: %s", target.name, exc)

    ifaces = mib.parse_interfaces(if_binds)
    interfaces = [
        InterfaceObs(
            if_index=i.if_index, if_name=i.key_name, if_alias=i.if_alias,
            if_type=i.if_type, mac_address=i.mac_address,
            speed_bps=i.speed_bps, admin_status=i.admin_status,
            oper_status=i.oper_status, counters=i.counters)
        for i in ifaces.values()]

    vendor = mib.infer_vendor(sysinfo.sys_object_id, sysinfo.sys_descr)

    lldp_local_chassis_mac = None
    lldp_local_sysname = None
    neighbors: list[NeighborObs] = []
    try:
        lldp_binds = walk(target, lldp.LLDP_ROOT, recorded)
    except SnmpEmpty:
        log.debug("%s: no LLDP data", target.name)
        lldp_binds = None
    except SnmpError as exc:
        log.debug("%s: LLDP walk failed: %s", target.name, exc)
        lldp_binds = None

    if lldp_binds is not None:
        view = lldp.parse(lldp_binds)
        lldp_local_chassis_mac = view.local_chassis_mac
        lldp_local_sysname = view.local_sysname
        for nbr in view.neighbors:
            neighbors.append(NeighborObs(
                local_if_name=view.local_interface_for(nbr),
                chassis_mac=nbr.chassis_mac,
                sys_name=nbr.sys_name,
                peer_if_name=nbr.remote_interface_name,
                sys_desc=nbr.sys_desc))

    return DeviceObs(
        poll_target=target.name,
        reachable=True,
        sys_name=sysinfo.sys_name,
        sys_descr=sysinfo.sys_descr,
        sys_object_id=sysinfo.sys_object_id,
        sys_location=sysinfo.sys_location,
        uptime_ticks=sysinfo.uptime_ticks,
        mgmt_ip=None if recorded else target.host,
        vendor=vendor,
        interfaces=interfaces,
        lldp_local_chassis_mac=lldp_local_chassis_mac,
        lldp_local_sysname=lldp_local_sysname,
        neighbors=neighbors,
    )


# --- transitional adapters --------------------------------------------
# poll_one() still persists through the same per-call store.* sequence
# it always has; these translate observe_one()'s wire shapes back into
# what that sequence expects. They go away in step 3, when --direct is
# rewired to call process_run() directly (the same conversion server/
# ingest.py already does for the API path).

_PolledIface = namedtuple(
    "_PolledIface",
    "if_index if_name if_alias if_type mac_address speed_bps "
    "admin_status oper_status counters key_name")


def _iface_view(interfaces: list[InterfaceObs]) -> dict[int, "_PolledIface"]:
    return {
        i.if_index: _PolledIface(
            if_index=i.if_index, if_name=i.if_name, if_alias=i.if_alias,
            if_type=i.if_type, mac_address=i.mac_address,
            speed_bps=i.speed_bps, admin_status=i.admin_status,
            oper_status=i.oper_status, counters=i.counters or {},
            key_name=i.if_name)
        for i in interfaces}


class _NbrView:
    def __init__(self, n: NeighborObs):
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


class _LldpView:
    def __init__(self, neighbors: list[NeighborObs]):
        self.neighbors = [_NbrView(n) for n in neighbors]

    def local_interface_for(self, nbr: "_NbrView") -> str | None:
        return nbr.local_if_name


POLLED: dict[str, str] = {}     # target name -> device_id


def poll_one(conn, tenant_id: str, target: SnmpTarget,
             recorded: str | None, dry_run: bool) -> bool:
    obs = observe_one(target, recorded)
    if not obs.reachable:
        return False

    ifaces = _iface_view(obs.interfaces)
    display = obs.sys_name or target.name

    log.info("%s: sysName=%s vendor=%s interfaces=%d",
             target.name, obs.sys_name, obs.vendor, len(ifaces))

    if dry_run:
        for i in sorted(ifaces.values(), key=lambda x: x.if_index):
            log.info("    [%s] %-20s %-10s %-8s mac=%s",
                     i.if_index, i.key_name, i.if_type or "-",
                     i.oper_status or "-", i.mac_address or "-")
        return True

    claims = claims_for(obs.sys_name, ifaces, target, recorded)
    device_id, created = store.resolve_device(
        conn, tenant_id, claims,
        display_name=display,
        mgmt_ip=obs.mgmt_ip,
        vendor=obs.vendor,
        os_version=obs.sys_descr)

    POLLED[target.name] = device_id
    seen, retired = store.upsert_interfaces(conn, tenant_id, device_id, ifaces)
    if_ids = store.interface_ids(conn, device_id)
    n_metrics = store.write_metrics(conn, tenant_id, device_id, ifaces, if_ids)
    conn.commit()

    log.info("%s: device=%s (%s) interfaces=%d stale=%d metrics=%d",
             target.name, device_id[:8], "new" if created else "existing",
             seen, retired, n_metrics)

    # --- LLDP ---------------------------------------------------------
    # Deliberately after the commit above. Interface rows must exist
    # before links can reference them, and a device with no LLDP is a
    # normal device, not a failed poll.
    extra = lldp_claims(obs.lldp_local_chassis_mac)
    if extra:
        store.resolve_device(conn, tenant_id, claims + extra,
                             display_name=display, mgmt_ip=None)

    view = _LldpView(obs.neighbors)
    counts = topology.build_links(conn, tenant_id, device_id, view)
    conn.commit()
    if counts["links"] or counts["unidentifiable"]:
        log.info("%s: lldp links=%d (interface=%d device=%d) "
                 "placeholders=%d unidentifiable=%d",
                 target.name, counts["links"], counts["interface"],
                 counts["device"], counts["placeholders"],
                 counts["unidentifiable"])
    return True


def poll_fdb(conn, tenant_id: str, target: SnmpTarget,
             recorded: str | None, device_id: str) -> None:
    """
    Second pass. Runs only after every device has been polled, because
    a MAC can only be attributed to a device that already exists in the
    database — inference over a half-populated inventory would resolve
    almost nothing and look like a broken parser.
    """
    try:
        binds = walk(target, fdb.BRIDGE_ROOT, recorded)
    except (SnmpEmpty, SnmpError):
        return

    view = fdb.parse(binds)
    if not view.entries:
        return

    owners = topology.mac_ownership(conn, tenant_id)
    candidates = fdb.find_leaf_ports(view, device_id, owners)
    if not candidates:
        log.debug("%s: fdb %d entries, no unambiguous leaf ports",
                  target.name, len(view.entries))
        return

    counts = topology.build_links_from_fdb(conn, tenant_id, device_id, candidates)
    conn.commit()
    log.info("%s: fdb inferred links=%d (interface=%d device=%d) from %d entries",
             target.name, counts["links"], counts["interface"],
             counts["device"], len(view.entries))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default="inventory.yaml")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s")

    cfg = yaml.safe_load(Path(args.inventory).read_text())
    targets = build_targets(cfg)
    if not targets:
        log.error("no devices in %s", args.inventory)
        return 1

    env = load_env(Path(args.env))
    dsn = os.environ.get("DB_URL") or env.get("DB_URL")
    tenant_id = os.environ.get("TENANT_ID") or env.get("TENANT_ID")

    conn = None
    run_id = None
    if not args.dry_run:
        if not dsn or not tenant_id:
            log.error("DB_URL and TENANT_ID required (set in .env)")
            return 1
        conn = store.connect(dsn)
        run_id = store.start_run(conn, tenant_id)
        conn.commit()

    ok = 0
    for target, recorded in targets:
        # Per-device isolation: one bad device must never abort the cycle.
        try:
            if poll_one(conn, tenant_id, target, recorded, args.dry_run):
                ok += 1
        except Exception:
            log.exception("%s: unhandled error", target.name)
            if conn:
                conn.rollback()

    if conn and not args.dry_run:
        # Second pass: FDB inference needs the complete device and
        # interface inventory to resolve MACs against.
        for target, recorded in targets:
            device_id = POLLED.get(target.name)
            if not device_id:
                continue
            try:
                poll_fdb(conn, tenant_id, target, recorded, device_id)
            except Exception:
                log.exception("%s: fdb pass failed", target.name)
                conn.rollback()

    if conn:
        # Confidence is a function of evidence, so it can only be
        # computed once every device has reported.
        roll = topology.rollup_confidence(conn, tenant_id)
        log.info("confidence rollup: scored=%d stale=%d",
                 roll["scored"], roll["stale"])
        store.finish_run(conn, run_id, ok)
        conn.commit()
        conn.close()

    log.info("polled %d/%d devices successfully", ok, len(targets))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
