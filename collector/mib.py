"""
Turn raw varbinds into normalized device and interface records.

OID constants only cover what the collector needs today. Vendor-specific
mappings are NOT authored here — adopt snmp_exporter / Kentik profiles
when you need them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- system group -------------------------------------------------------
SYS_DESCR = ".1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = ".1.3.6.1.2.1.1.2.0"
SYS_UPTIME = ".1.3.6.1.2.1.1.3.0"
SYS_CONTACT = ".1.3.6.1.2.1.1.4.0"
SYS_NAME = ".1.3.6.1.2.1.1.5.0"
SYS_LOCATION = ".1.3.6.1.2.1.1.6.0"

SYSTEM_ROOT = ".1.3.6.1.2.1.1"

# --- IF-MIB -------------------------------------------------------------
IF_DESCR = ".1.3.6.1.2.1.2.2.1.2"
IF_TYPE = ".1.3.6.1.2.1.2.2.1.3"
IF_SPEED = ".1.3.6.1.2.1.2.2.1.5"
IF_PHYS_ADDR = ".1.3.6.1.2.1.2.2.1.6"
IF_ADMIN_STATUS = ".1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = ".1.3.6.1.2.1.2.2.1.8"

IF_NAME = ".1.3.6.1.2.1.31.1.1.1.1"
IF_ALIAS = ".1.3.6.1.2.1.31.1.1.1.18"
IF_HIGH_SPEED = ".1.3.6.1.2.1.31.1.1.1.15"   # Mbps
IF_HC_IN_OCTETS = ".1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCTETS = ".1.3.6.1.2.1.31.1.1.1.10"

IF_MIB_ROOT = ".1.3.6.1.2.1.2"
IF_XMIB_ROOT = ".1.3.6.1.2.1.31"

_STATUS = {"1": "up", "2": "down", "3": "testing",
           "4": "unknown", "5": "dormant", "6": "notPresent",
           "7": "lowerLayerDown"}

# IANAifType -> coarse class. Deliberately small; unmapped values pass
# through as "other" rather than being guessed at.
_IFTYPE = {
    "6": "ethernet", "24": "loopback", "53": "virtual", "131": "tunnel",
    "135": "l2vlan", "136": "l3vlan", "161": "lag", "71": "wireless",
    "1": "other", "117": "ethernet",
}


@dataclass
class SystemInfo:
    sys_name: str | None = None
    sys_descr: str | None = None
    sys_object_id: str | None = None
    sys_location: str | None = None
    sys_contact: str | None = None
    uptime_ticks: int | None = None


@dataclass
class InterfaceInfo:
    if_index: int
    if_name: str | None = None
    if_descr: str | None = None
    if_alias: str | None = None
    if_type: str | None = None
    mac_address: str | None = None
    speed_bps: int | None = None
    admin_status: str | None = None
    oper_status: str | None = None
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def key_name(self) -> str:
        """
        Interface identity. ifName preferred, ifDescr fallback.

        ifIndex is NOT used as identity — it is reassigned across reboots
        and when virtual interfaces churn. Keying on it silently moves
        metrics between ports.
        """
        return self.if_name or self.if_descr or f"ifIndex-{self.if_index}"


def _index_of(oid: str, root: str) -> str | None:
    if not oid.startswith(root + "."):
        return None
    return oid[len(root) + 1:]


def _to_int(value: str) -> int | None:
    m = re.search(r"-?\d+", value or "")
    return int(m.group()) if m else None


def _normalize_mac(value: str, vtype: str | None) -> str | None:
    """net-snmp emits MACs as Hex-STRING 'AA BB CC ...' or as raw bytes."""
    if not value:
        return None
    hexed = re.findall(r"[0-9A-Fa-f]{2}", value)
    if len(hexed) != 6:
        return None
    mac = ":".join(h.lower() for h in hexed)
    return None if mac == "00:00:00:00:00:00" else mac


def parse_system(binds) -> SystemInfo:
    by_oid = {vb.oid: vb for vb in binds}
    get = lambda o: by_oid[o].value if o in by_oid else None
    return SystemInfo(
        sys_name=get(SYS_NAME),
        sys_descr=get(SYS_DESCR),
        sys_object_id=get(SYS_OBJECT_ID),
        sys_location=get(SYS_LOCATION),
        sys_contact=get(SYS_CONTACT),
        uptime_ticks=_to_int(get(SYS_UPTIME) or ""),
    )


def parse_interfaces(binds) -> dict[int, InterfaceInfo]:
    """
    Build interfaces keyed by ifIndex for assembly only.

    ifIndex is the correlation key WITHIN a single poll — every IF-MIB
    column is indexed by it. It is not persisted as identity.
    """
    ifaces: dict[int, InterfaceInfo] = {}

    def slot(idx_str: str) -> InterfaceInfo | None:
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            return None
        return ifaces.setdefault(idx, InterfaceInfo(if_index=idx))

    for vb in binds:
        for root, setter in (
            (IF_NAME, lambda i, v, t: setattr(i, "if_name", v)),
            (IF_DESCR, lambda i, v, t: setattr(i, "if_descr", v)),
            (IF_ALIAS, lambda i, v, t: setattr(i, "if_alias", v or None)),
            (IF_TYPE, lambda i, v, t: setattr(i, "if_type", _IFTYPE.get(str(_to_int(v)), "other"))),
            (IF_PHYS_ADDR, lambda i, v, t: setattr(i, "mac_address", _normalize_mac(v, t))),
            (IF_ADMIN_STATUS, lambda i, v, t: setattr(i, "admin_status", _STATUS.get(str(_to_int(v)), "unknown"))),
            (IF_OPER_STATUS, lambda i, v, t: setattr(i, "oper_status", _STATUS.get(str(_to_int(v)), "unknown"))),
        ):
            idx = _index_of(vb.oid, root)
            if idx is not None:
                iface = slot(idx)
                if iface:
                    setter(iface, vb.value, vb.type)
                break

    # Speed: prefer ifHighSpeed (Mbps, 64-bit safe) over ifSpeed, which
    # saturates at 4.29 Gbps and lies on 10G+ links.
    for vb in binds:
        idx = _index_of(vb.oid, IF_HIGH_SPEED)
        if idx is not None:
            iface = slot(idx)
            mbps = _to_int(vb.value)
            if iface and mbps:
                iface.speed_bps = mbps * 1_000_000
    for vb in binds:
        idx = _index_of(vb.oid, IF_SPEED)
        if idx is not None:
            iface = slot(idx)
            bps = _to_int(vb.value)
            if iface and iface.speed_bps is None and bps:
                iface.speed_bps = bps

    for vb in binds:
        for root, metric in ((IF_HC_IN_OCTETS, "if_in_octets"),
                             (IF_HC_OUT_OCTETS, "if_out_octets")):
            idx = _index_of(vb.oid, root)
            if idx is not None:
                iface = slot(idx)
                val = _to_int(vb.value)
                if iface and val is not None:
                    iface.counters[metric] = val
                break

    return ifaces


def is_hardware_mac(mac: str | None) -> bool:
    """
    True only for burned-in addresses.

    Bit 0x02 of the first octet is the locally-administered flag. Linux
    bridges, veths and docker0 all set it and REGENERATE the address on
    every container restart. Using one as a device identity anchor means
    the device loses its identity the next time Docker restarts — the
    exact failure identity resolution exists to prevent.
    """
    if not mac:
        return False
    try:
        first = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return False
    if first & 0x02:            # locally administered
        return False
    if mac == "00:00:00:00:00:00":
        return False
    return True


def infer_vendor(sys_object_id: str | None, sys_descr: str | None) -> str | None:
    """
    Coarse vendor from enterprise OID. Small on purpose — this is a
    convenience label, not the normalization layer.
    """
    ent = {
        ".1.3.6.1.4.1.9": "cisco",
        ".1.3.6.1.4.1.2636": "juniper",
        ".1.3.6.1.4.1.12356": "fortinet",
        ".1.3.6.1.4.1.674": "dell",
        ".1.3.6.1.4.1.11": "hp",
        ".1.3.6.1.4.1.8072": "net-snmp",
        ".1.3.6.1.4.1.29671": "meraki",
        ".1.3.6.1.4.1.4526": "netgear",
        ".1.3.6.1.4.1.11863": "tp-link",
        ".1.3.6.1.4.1.318": "apc",
        ".1.3.6.1.4.1.6876": "vmware",
    }
    if sys_object_id:
        for prefix, name in ent.items():
            if sys_object_id.startswith(prefix):
                return name
    if sys_descr and "linux" in sys_descr.lower():
        return "linux"
    return None
