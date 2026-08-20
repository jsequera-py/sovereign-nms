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
from pathlib import Path

import yaml

from common.wire import DeviceObs, FdbObs, InterfaceObs, NeighborObs
from . import fdb, lldp, mib, store
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

    # --- FDB ------------------------------------------------------------
    # Parsing only — inference needs the whole fleet's MAC ownership,
    # which only process_run() has. See collector/fdb.py.
    fdb_obs = None
    try:
        bridge_binds = walk(target, fdb.BRIDGE_ROOT, recorded)
    except (SnmpEmpty, SnmpError):
        bridge_binds = None

    if bridge_binds:
        fdb_view = fdb.parse(bridge_binds)
        if fdb_view.entries:
            fdb_obs = FdbObs(port_ifindex=fdb_view.port_ifindex,
                             ports=fdb_view.by_port())

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
        fdb=fdb_obs,
    )


def _log_dry_run(target: SnmpTarget, obs: DeviceObs) -> None:
    log.info("%s: sysName=%s vendor=%s interfaces=%d",
             target.name, obs.sys_name, obs.vendor, len(obs.interfaces))
    for i in sorted(obs.interfaces, key=lambda x: x.if_index):
        log.info("    [%s] %-20s %-10s %-8s mac=%s",
                 i.if_index, i.if_name, i.if_type or "-",
                 i.oper_status or "-", i.mac_address or "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default="inventory.yaml")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--direct", action="store_true",
                    help="persist locally against DB_URL, instead of "
                         "posting to the ingest API (API mode: step 4)")
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

    if args.dry_run:
        ok = 0
        for target, recorded in targets:
            try:
                obs = observe_one(target, recorded)
            except Exception:
                log.exception("%s: unhandled error", target.name)
                continue
            if not obs.reachable:
                continue
            _log_dry_run(target, obs)
            ok += 1
        log.info("observed %d/%d devices successfully", ok, len(targets))
        return 0 if ok else 2

    if not args.direct:
        log.error("API mode is not implemented yet (Phase 1.1 step 4) — "
                  "pass --direct")
        return 1

    env = load_env(Path(args.env))
    dsn = os.environ.get("DB_URL") or env.get("DB_URL")
    tenant_id = os.environ.get("TENANT_ID") or env.get("TENANT_ID")
    if not dsn or not tenant_id:
        log.error("DB_URL and TENANT_ID required (set in .env)")
        return 1

    devices: list[DeviceObs] = []
    for target, recorded in targets:
        # Per-device isolation: one bad device must never abort the cycle.
        try:
            devices.append(observe_one(target, recorded))
        except Exception:
            log.exception("%s: unhandled error", target.name)

    ok = 0
    for obs in devices:
        if obs.reachable:
            ok += 1
        else:
            log.warning("%s: %s (%s)", obs.poll_target, obs.error, obs.reach_status)

    # Lazy: a collector shipped without server/ must still run in API
    # mode. --direct is a local-debugging path on the central node only.
    from server import ingest

    conn = store.connect(dsn)
    result = ingest.process_run(
        conn, tenant_id=tenant_id, site_id=None, collector_key_id=None,
        collector_name="direct", devices=devices)
    conn.commit()
    conn.close()

    log.info("run=%s devices=%d unreachable=%d interfaces=%d "
             "lldp_links=%d fdb_links=%d auto_edge_pct=%s",
             result["run_id"], result["devices_ingested"],
             result["devices_unreachable"], result["interfaces"],
             result["lldp_links"], result["fdb_inferred_links"],
             result["auto_edge_pct"])

    log.info("polled %d/%d devices successfully", ok, len(targets))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
