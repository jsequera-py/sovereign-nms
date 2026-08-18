"""
LLDP-MIB parsing.

Written against two very different real implementations:

  Cisco-style (and the simulated fleet)
    lldpRemLocalPortNum -> a real local port
    lldpLocPortId subtype 5 (interfaceName)
    chassis id as Hex-STRING
    -> yields interface-level links

  MikroTik RouterOS 6.49 (verified on an RB951G-2HnD)
    lldpRemLocalPortNum = 0 for every neighbour
    lldpLocChassisId absent entirely
    lldpLocPortId subtype 3 (macAddress) — the NAME is in PortDesc
    chassis id as a plain STRING "E4:8D:8C:46:D0:1A"
    -> yields device-level links only

Neither is broken. The MIB permits both. A parser that assumes the
first shape silently finds nothing on the second.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- local ---
LOC_CHASSIS_SUBTYPE = ".1.0.8802.1.1.2.1.3.1.0"
LOC_CHASSIS_ID = ".1.0.8802.1.1.2.1.3.2.0"
LOC_SYSNAME = ".1.0.8802.1.1.2.1.3.3.0"
LOC_SYSDESC = ".1.0.8802.1.1.2.1.3.4.0"
LOC_PORT_SUBTYPE = ".1.0.8802.1.1.2.1.3.7.1.2"
LOC_PORT_ID = ".1.0.8802.1.1.2.1.3.7.1.3"
LOC_PORT_DESC = ".1.0.8802.1.1.2.1.3.7.1.4"

# --- remote ---
REM_CHASSIS_SUBTYPE = ".1.0.8802.1.1.2.1.4.1.1.4"
REM_CHASSIS_ID = ".1.0.8802.1.1.2.1.4.1.1.5"
REM_PORT_SUBTYPE = ".1.0.8802.1.1.2.1.4.1.1.6"
REM_PORT_ID = ".1.0.8802.1.1.2.1.4.1.1.7"
REM_PORT_DESC = ".1.0.8802.1.1.2.1.4.1.1.8"
REM_SYSNAME = ".1.0.8802.1.1.2.1.4.1.1.9"
REM_SYSDESC = ".1.0.8802.1.1.2.1.4.1.1.10"

LLDP_ROOT = ".1.0.8802.1.1.2.1"

# LldpPortIdSubtype / LldpChassisIdSubtype
SUBTYPE_MAC = "4"          # chassis: macAddress
SUBTYPE_PORT_MAC = "3"     # port:    macAddress
SUBTYPE_IFNAME = "5"       # port:    interfaceName
SUBTYPE_LOCAL = "7"        # port:    local (vendor string)

# sysName values that identify a product line, not a device. Using one
# as an identity key merges every unconfigured unit of that model.
GENERIC_SYSNAMES = {
    "mikrotik", "switch", "router", "ap", "accesspoint",
    "localhost", "unknown", "default", "openwrt", "raspberrypi",
}

_MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2})(?=(?:[:\- ]?[0-9A-Fa-f]{2}){5}\b)")


def parse_mac(value: str | None) -> str | None:
    """
    Accept every serialization seen in the wild:
      "64 C9 01 A9 42 7E"   net-snmp Hex-STRING
      "64:C9:01:A9:42:7E"   MikroTik plain STRING
      "64c901a9427e"        bare hex
    Rejects text that merely contains hex-looking characters.
    """
    if not value:
        return None
    cleaned = value.strip().strip('"')
    compact = re.sub(r"[:\-\s.]", "", cleaned)
    if len(compact) == 12 and re.fullmatch(r"[0-9A-Fa-f]{12}", compact):
        mac = ":".join(compact[i:i + 2].lower() for i in range(0, 12, 2))
        return None if mac == "00:00:00:00:00:00" else mac
    return None


@dataclass
class LocalPort:
    """One row of lldpLocPortTable."""
    port_num: int
    subtype: str | None = None
    port_id: str | None = None
    port_desc: str | None = None

    @property
    def interface_name(self) -> str | None:
        """
        Best guess at the local interface name.

        Subtype 5 means PortId IS the name. Subtype 3 means PortId is a
        MAC and the name lives in PortDesc — the MikroTik case. Getting
        this backwards makes every local port unresolvable.
        """
        if self.subtype == SUBTYPE_IFNAME and self.port_id:
            return self.port_id
        if self.port_desc:
            return self.port_desc
        if self.port_id and not parse_mac(self.port_id):
            return self.port_id
        return None


@dataclass
class Neighbor:
    """One row of lldpRemTable."""
    local_port_num: int
    rem_index: int
    chassis_subtype: str | None = None
    chassis_id: str | None = None
    port_subtype: str | None = None
    port_id: str | None = None
    port_desc: str | None = None
    sys_name: str | None = None
    sys_desc: str | None = None

    @property
    def chassis_mac(self) -> str | None:
        if self.chassis_subtype and self.chassis_subtype != SUBTYPE_MAC:
            return None
        return parse_mac(self.chassis_id)

    @property
    def remote_interface_name(self) -> str | None:
        if self.port_subtype == SUBTYPE_IFNAME and self.port_id:
            return self.port_id
        if self.port_desc:
            return self.port_desc
        if self.port_id and not parse_mac(self.port_id):
            return self.port_id
        return None

    @property
    def usable_sysname(self) -> str | None:
        """sysName, unless it is a factory default shared by a whole model."""
        if not self.sys_name:
            return None
        if self.sys_name.strip().lower() in GENERIC_SYSNAMES:
            return None
        return self.sys_name

    def is_identifiable(self) -> bool:
        """Enough to tell this neighbour apart from any other."""
        return bool(self.chassis_mac or self.usable_sysname)


@dataclass
class LldpView:
    local_chassis_mac: str | None
    local_sysname: str | None
    local_ports: dict[int, LocalPort]
    neighbors: list[Neighbor]

    def local_interface_for(self, nbr: Neighbor) -> str | None:
        """
        Which of MY interfaces is this neighbour on?

        Returns None when the device does not say — RouterOS reports
        localPortNum = 0 for all neighbours. That is the difference
        between an interface-level and a device-level link, and it must
        be represented, not guessed.
        """
        if nbr.local_port_num == 0:
            return None
        port = self.local_ports.get(nbr.local_port_num)
        return port.interface_name if port else None


def _suffix(oid: str, root: str) -> str | None:
    return oid[len(root) + 1:] if oid.startswith(root + ".") else None


def parse(binds) -> LldpView:
    by_oid = {vb.oid: vb for vb in binds}

    local_chassis = None
    if LOC_CHASSIS_ID in by_oid:
        subtype = by_oid.get(LOC_CHASSIS_SUBTYPE)
        if subtype is None or subtype.value.strip() == SUBTYPE_MAC:
            local_chassis = parse_mac(by_oid[LOC_CHASSIS_ID].value)

    local_sysname = by_oid[LOC_SYSNAME].value if LOC_SYSNAME in by_oid else None
    if local_sysname and local_sysname.strip().lower() in GENERIC_SYSNAMES:
        local_sysname = None

    ports: dict[int, LocalPort] = {}

    def port_slot(idx: str) -> LocalPort | None:
        try:
            n = int(idx)
        except ValueError:
            return None
        return ports.setdefault(n, LocalPort(port_num=n))

    for vb in binds:
        for root, field in ((LOC_PORT_SUBTYPE, "subtype"),
                            (LOC_PORT_ID, "port_id"),
                            (LOC_PORT_DESC, "port_desc")):
            idx = _suffix(vb.oid, root)
            if idx is not None:
                p = port_slot(idx)
                if p:
                    setattr(p, field, vb.value.strip())
                break

    neighbors: dict[tuple[int, int], Neighbor] = {}

    def nbr_slot(idx: str) -> Neighbor | None:
        # index is timeMark.localPortNum.remIndex
        parts = idx.split(".")
        if len(parts) != 3:
            return None
        try:
            _, local_port, rem_index = (int(x) for x in parts)
        except ValueError:
            return None
        return neighbors.setdefault(
            (local_port, rem_index),
            Neighbor(local_port_num=local_port, rem_index=rem_index))

    for vb in binds:
        for root, field in ((REM_CHASSIS_SUBTYPE, "chassis_subtype"),
                            (REM_CHASSIS_ID, "chassis_id"),
                            (REM_PORT_SUBTYPE, "port_subtype"),
                            (REM_PORT_ID, "port_id"),
                            (REM_PORT_DESC, "port_desc"),
                            (REM_SYSNAME, "sys_name"),
                            (REM_SYSDESC, "sys_desc")):
            idx = _suffix(vb.oid, root)
            if idx is not None:
                n = nbr_slot(idx)
                if n:
                    val = vb.value.strip()
                    setattr(n, field, val or None)
                break

    return LldpView(
        local_chassis_mac=local_chassis,
        local_sysname=local_sysname,
        local_ports=ports,
        neighbors=list(neighbors.values()),
    )
