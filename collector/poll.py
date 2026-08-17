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

from . import mib, store
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


def claims_for(sysinfo: mib.SystemInfo, ifaces, target: SnmpTarget,
               recorded: str | None) -> list[store.IdentityClaim]:
    """
    Every identifier this poll observed. Weak ones are included on
    purpose — an identifier that misses today becomes the anchor that
    survives a renumbering tomorrow.
    """
    claims: list[store.IdentityClaim] = []
    if sysinfo.sys_name:
        claims.append(store.IdentityClaim("sysname", sysinfo.sys_name, "inferred"))

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


def poll_one(conn, tenant_id: str, target: SnmpTarget,
             recorded: str | None, dry_run: bool) -> bool:
    try:
        sys_binds = walk(target, mib.SYSTEM_ROOT, recorded)
    except SnmpEmpty:
        log.error("%s: reachable but system MIB is empty — SNMP view is "
                  "restricted, not a failure", target.name)
        return False
    except SnmpError as exc:
        log.error("%s: unreachable — %s", target.name, exc)
        return False

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
    vendor = mib.infer_vendor(sysinfo.sys_object_id, sysinfo.sys_descr)
    display = sysinfo.sys_name or target.name

    log.info("%s: sysName=%s vendor=%s interfaces=%d",
             target.name, sysinfo.sys_name, vendor, len(ifaces))

    if dry_run:
        for i in sorted(ifaces.values(), key=lambda x: x.if_index):
            log.info("    [%s] %-20s %-10s %-8s mac=%s",
                     i.if_index, i.key_name, i.if_type or "-",
                     i.oper_status or "-", i.mac_address or "-")
        return True

    claims = claims_for(sysinfo, ifaces, target, recorded)
    device_id, created = store.resolve_device(
        conn, tenant_id, claims,
        display_name=display,
        mgmt_ip=None if recorded else target.host,
        vendor=vendor,
        os_version=sysinfo.sys_descr)

    seen, retired = store.upsert_interfaces(conn, tenant_id, device_id, ifaces)
    if_ids = store.interface_ids(conn, device_id)
    n_metrics = store.write_metrics(conn, tenant_id, device_id, ifaces, if_ids)
    conn.commit()

    log.info("%s: device=%s (%s) interfaces=%d stale=%d metrics=%d",
             target.name, device_id[:8], "new" if created else "existing",
             seen, retired, n_metrics)
    return True


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

    if conn:
        store.finish_run(conn, run_id, ok)
        conn.commit()
        conn.close()

    log.info("polled %d/%d devices successfully", ok, len(targets))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
