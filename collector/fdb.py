"""
Bridge forwarding-database parsing.

FDB inference is fundamentally different from LLDP. LLDP is a device
ASSERTING "my neighbour is X". FDB is a record of which MACs were seen
on which port — adjacency has to be inferred, and inference can be
wrong.

The strict rule implemented here only claims a link when the evidence
admits one interpretation:

    a bridge port on which exactly ONE known device appears,
    with few enough total MACs that nothing can be hiding behind it

That finds leaf attachments — hosts, hypervisors, APs — which is where
LLDP most often goes missing. It deliberately does NOT find switch-to-
switch links, because an uplink port carries the whole network and no
amount of counting distinguishes "adjacent" from "reachable".

Loosening this rule is how false links get created, and a false link
becomes a false dependency, which suppresses alerts for a real outage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# dot1dBasePort -> ifIndex. Bridge port numbers are NOT ifIndexes on
# real hardware; assuming they are attributes MACs to the wrong port.
BASE_PORT_IFINDEX = ".1.3.6.1.2.1.17.1.4.1.2"

FDB_ADDR = ".1.3.6.1.2.1.17.4.3.1.1"
FDB_PORT = ".1.3.6.1.2.1.17.4.3.1.2"
FDB_STATUS = ".1.3.6.1.2.1.17.4.3.1.3"

BRIDGE_ROOT = ".1.3.6.1.2.1.17"

STATUS_LEARNED = "3"

# A directly attached device presents its own MACs and nothing else.
# More than this on one port means something is relaying behind it —
# an unmanaged switch, a hypervisor bridging VMs, an AP with clients.
# Raising this trades precision for recall; the scorer will tell you
# immediately which way it went.
MAX_LEAF_MACS = 6


@dataclass
class FdbEntry:
    mac: str
    bridge_port: int
    status: str | None = None

    @property
    def learned(self) -> bool:
        # Self (4) and mgmt (5) entries describe the switch itself.
        # Only dynamically learned entries say anything about who is
        # attached.
        return self.status is None or self.status == STATUS_LEARNED


@dataclass
class FdbView:
    port_ifindex: dict[int, int] = field(default_factory=dict)
    entries: list[FdbEntry] = field(default_factory=list)

    def by_port(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for e in self.entries:
            if e.learned:
                out.setdefault(e.bridge_port, []).append(e.mac)
        return out


def _oid_mac_suffix(suffix: str) -> str | None:
    parts = suffix.split(".")
    if len(parts) != 6:
        return None
    try:
        return ":".join(f"{int(p):02x}" for p in parts)
    except ValueError:
        return None


def _suffix(oid: str, root: str) -> str | None:
    return oid[len(root) + 1:] if oid.startswith(root + ".") else None


def parse(binds) -> FdbView:
    view = FdbView()
    by_mac: dict[str, FdbEntry] = {}

    for vb in binds:
        idx = _suffix(vb.oid, BASE_PORT_IFINDEX)
        if idx is not None:
            try:
                view.port_ifindex[int(idx)] = int(vb.value.strip())
            except ValueError:
                pass
            continue

        for root, field_name in ((FDB_PORT, "bridge_port"),
                                 (FDB_STATUS, "status")):
            sfx = _suffix(vb.oid, root)
            if sfx is None:
                continue
            mac = _oid_mac_suffix(sfx)
            if not mac:
                break
            entry = by_mac.setdefault(mac, FdbEntry(mac=mac, bridge_port=-1))
            val = vb.value.strip()
            if field_name == "bridge_port":
                try:
                    entry.bridge_port = int(val)
                except ValueError:
                    pass
            else:
                entry.status = val
            break

    view.entries = [e for e in by_mac.values() if e.bridge_port >= 0]
    return view


@dataclass
class LeafCandidate:
    """A port that faces exactly one known device, unambiguously."""
    bridge_port: int
    local_ifindex: int | None
    peer_device_id: str
    peer_interface_id: str | None
    mac_count: int
    evidence_macs: list[str]


def find_leaf_ports(view: FdbView, self_device_id: str,
                    mac_owner: dict[str, tuple[str, str | None]]
                    ) -> list[LeafCandidate]:
    """
    Apply the strict rule.

    mac_owner maps a MAC to (device_id, interface_id | None). MACs not
    in it are unknown endpoints — workstations, printers, phones. They
    still count toward the total, because their presence is exactly
    what signals something is relaying behind the port.
    """
    candidates: list[LeafCandidate] = []

    for bridge_port, macs in view.by_port().items():
        if len(macs) > MAX_LEAF_MACS:
            continue                      # transit port: too much behind it

        owners = {}
        for mac in macs:
            owner = mac_owner.get(mac)
            if owner and owner[0] != self_device_id:
                owners.setdefault(owner[0], []).append((mac, owner[1]))

        if len(owners) != 1:
            # Zero known devices: only unknown endpoints, nothing to link.
            # Two or more: the port cannot be facing both directly.
            continue

        peer_device_id, hits = next(iter(owners.items()))
        peer_interface_id = None
        distinct_ifaces = {i for _, i in hits if i}
        if len(distinct_ifaces) == 1:
            peer_interface_id = next(iter(distinct_ifaces))
        # More than one interface of the same peer on this port means we
        # cannot say which cable it is — fall back to device fidelity
        # rather than picking one.

        candidates.append(LeafCandidate(
            bridge_port=bridge_port,
            local_ifindex=view.port_ifindex.get(bridge_port),
            peer_device_id=peer_device_id,
            peer_interface_id=peer_interface_id,
            mac_count=len(macs),
            evidence_macs=sorted(m for m, _ in hits),
        ))

    return candidates
