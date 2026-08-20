"""
The contract between a collector and the ingest API.

Deliberately dumb: a collector reports WHAT IT SAW, never what it
concluded. No device_id, no link, no tenant_id — those are all
server-side conclusions that require the full database to reach.

That split is what makes both deployment models work:

  - a collector cannot resolve identity, because the device it is
    looking at may already exist under a different address at another
    site it has never seen
  - a collector cannot claim a tenant, because the server derives that
    from its credential

It also means a collector can be dropped into a customer network with
no state of its own, and losing one loses nothing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

WIRE_VERSION = 1


@dataclass
class InterfaceObs:
    if_index: int
    if_name: str
    if_alias: str | None = None
    if_type: str | None = None
    mac_address: str | None = None
    speed_bps: int | None = None
    admin_status: str | None = None
    oper_status: str | None = None
    counters: dict[str, int] = field(default_factory=dict)


@dataclass
class NeighborObs:
    """One LLDP remote-table row, already normalised for vendor quirks."""
    local_if_name: str | None      # None when the device does not say
    chassis_mac: str | None
    sys_name: str | None
    peer_if_name: str | None
    sys_desc: str | None = None


@dataclass
class FdbObs:
    """
    Raw forwarding table. Leaf analysis happens server-side because it
    needs to know which MACs belong to devices — knowledge a single
    collector does not have.
    """
    port_ifindex: dict[int, int] = field(default_factory=dict)
    ports: dict[int, list[str]] = field(default_factory=dict)


@dataclass
class DeviceObs:
    poll_target: str
    reachable: bool = True
    error: str | None = None
    reach_status: str = "ok"  # "ok" | "unreachable" | "filtered"

    sys_name: str | None = None
    sys_descr: str | None = None
    sys_object_id: str | None = None
    sys_location: str | None = None
    uptime_ticks: int | None = None
    mgmt_ip: str | None = None
    vendor: str | None = None

    interfaces: list[InterfaceObs] = field(default_factory=list)

    lldp_local_chassis_mac: str | None = None
    lldp_local_sysname: str | None = None
    neighbors: list[NeighborObs] = field(default_factory=list)

    fdb: FdbObs | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunPayload:
    wire_version: int = WIRE_VERSION
    collector_name: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    devices: list[DeviceObs] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "wire_version": self.wire_version,
            "collector_name": self.collector_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "devices": [d.to_json() for d in self.devices],
        }


def device_from_json(d: dict[str, Any]) -> DeviceObs:
    fdb_raw = d.get("fdb")
    fdb_obs = None
    if fdb_raw:
        fdb_obs = FdbObs(
            # JSON object keys are strings; bridge ports are integers.
            port_ifindex={int(k): int(v) for k, v in (fdb_raw.get("port_ifindex") or {}).items()},
            ports={int(k): list(v) for k, v in (fdb_raw.get("ports") or {}).items()},
        )
    return DeviceObs(
        poll_target=d["poll_target"],
        reachable=d.get("reachable", True),
        error=d.get("error"),
        reach_status=d.get("reach_status", "ok"),
        sys_name=d.get("sys_name"),
        sys_descr=d.get("sys_descr"),
        sys_object_id=d.get("sys_object_id"),
        sys_location=d.get("sys_location"),
        uptime_ticks=d.get("uptime_ticks"),
        mgmt_ip=d.get("mgmt_ip"),
        vendor=d.get("vendor"),
        interfaces=[InterfaceObs(**i) for i in d.get("interfaces", [])],
        lldp_local_chassis_mac=d.get("lldp_local_chassis_mac"),
        lldp_local_sysname=d.get("lldp_local_sysname"),
        neighbors=[NeighborObs(**n) for n in d.get("neighbors", [])],
        fdb=fdb_obs,
    )
