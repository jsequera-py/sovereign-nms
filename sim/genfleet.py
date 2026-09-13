


#!/usr/bin/env python3
"""
Generate an snmpsim fleet from sim/topology.yaml.

Produces one .snmprec per device containing:
  - system group (sysDescr / sysObjectID / sysName / sysUpTime)
  - IF-MIB + ifXTable for every port
  - LLDP local chassis + port tables      (if the profile supports LLDP)
  - LLDP remote table, derived from links (respecting per-link lldp mode)
  - dot1dTpFdbTable  (MAC forwarding) so links without LLDP still leave
    evidence that inference can pick up

Run:
    python sim/genfleet.py
    python sim/genfleet.py --out sim/data --inventory inventory.generated.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

# --- OID roots ----------------------------------------------------------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

IF_NUMBER = "1.3.6.1.2.1.2.1.0"
IF_INDEX = "1.3.6.1.2.1.2.2.1.1"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_MTU = "1.3.6.1.2.1.2.2.1.4"
IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
IF_PHYS = "1.3.6.1.2.1.2.2.1.6"
IF_ADMIN = "1.3.6.1.2.1.2.2.1.7"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"

IFX_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IFX_HCIN = "1.3.6.1.2.1.31.1.1.1.6"
IFX_HCOUT = "1.3.6.1.2.1.31.1.1.1.10"
IFX_HISPEED = "1.3.6.1.2.1.31.1.1.1.15"
IFX_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

# LLDP-MIB (note: 1.0.8802, not 1.3.6.1)
LLDP_LOC_CHASSIS_SUBTYPE = "1.0.8802.1.1.2.1.3.1.0"
LLDP_LOC_CHASSIS_ID = "1.0.8802.1.1.2.1.3.2.0"
LLDP_LOC_SYSNAME = "1.0.8802.1.1.2.1.3.3.0"
LLDP_LOC_SYSDESC = "1.0.8802.1.1.2.1.3.4.0"
LLDP_LOC_PORT_SUBTYPE = "1.0.8802.1.1.2.1.3.7.1.2"
LLDP_LOC_PORT_ID = "1.0.8802.1.1.2.1.3.7.1.3"
LLDP_LOC_PORT_DESC = "1.0.8802.1.1.2.1.3.7.1.4"

LLDP_REM_CHASSIS_SUBTYPE = "1.0.8802.1.1.2.1.4.1.1.4"
LLDP_REM_CHASSIS_ID = "1.0.8802.1.1.2.1.4.1.1.5"
LLDP_REM_PORT_SUBTYPE = "1.0.8802.1.1.2.1.4.1.1.6"
LLDP_REM_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.7"
LLDP_REM_PORT_DESC = "1.0.8802.1.1.2.1.4.1.1.8"
LLDP_REM_SYSNAME = "1.0.8802.1.1.2.1.4.1.1.9"
LLDP_REM_SYSDESC = "1.0.8802.1.1.2.1.4.1.1.10"

# BRIDGE-MIB forwarding database
DOT1D_FDB_ADDR = "1.3.6.1.2.1.17.4.3.1.1"
DOT1D_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
DOT1D_FDB_STATUS = "1.3.6.1.2.1.17.4.3.1.3"
# dot1dBasePort -> ifIndex. Deliberately NOT an identity mapping here:
# on real switches bridge port numbers and ifIndex differ, and code
# that conflates them attributes MACs to the wrong interface.
DOT1D_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"

# snmprec type tags
T_OCTET = "4"
T_INT = "2"
T_OID = "6"
T_TIMETICKS = "67"
T_GAUGE = "66"
T_COUNTER64 = "70"
T_HEX = "4x"


def stable_mac(seed: str, local: bool = False) -> str:
    """
    Deterministic MAC from a name. Globally-administered by default so
    the collector's hardware-MAC filter accepts it.
    """
    h = hashlib.sha256(seed.encode()).hexdigest()
    octets = [int(h[i:i + 2], 16) for i in range(0, 12, 2)]
    octets[0] = (octets[0] | 0x02) if local else (octets[0] & 0xFE & ~0x02 | 0x00)
    octets[0] &= 0xFE            # never multicast
    if not local:
        octets[0] &= ~0x02 & 0xFF
    return ":".join(f"{o:02x}" for o in octets)


def mac_hex(mac: str) -> str:
    return mac.replace(":", "").lower()


def mac_oid_suffix(mac: str) -> str:
    return ".".join(str(int(p, 16)) for p in mac.split(":"))


class Device:
    def __init__(self, spec: dict, profile: dict):
        self.name = spec["name"]
        self.mgmt = spec["mgmt"]
        self.role = spec.get("role", "unknown")
        self.site = spec.get("site", "default")
        self.profile = profile
        self.lldp = bool(profile.get("lldp", False))
        # Vendors differ in how much of LLDP-MIB they populate.
        #   "full"    - localPortNum + chassis id (Cisco-like)
        #   "partial" - neighbours listed, localPortNum=0, no chassis id
        #               (MikroTik RouterOS 6.49, verified on RB951G)
        self.lldp_quality = profile.get("lldp_quality", "full")
        self.port_count = int(profile.get("port_count", 8))
        self.prefix = profile.get("port_prefix", "eth")
        self.speed_mbps = int(profile.get("speed_mbps", 1000))
        self.chassis_mac = stable_mac(f"chassis:{self.name}")
        # ifIndex 1 is loopback/mgmt; physical ports start at 2. Matches
        # how most real switches number things.
        self.ports = {p: p + 1 for p in range(1, self.port_count + 1)}

    def port_name(self, port: int) -> str:
        if self.prefix.endswith("/"):
            return f"{self.prefix}{port}"
        return f"{self.prefix}{port}"

    def port_mac(self, port: int) -> str:
        return stable_mac(f"{self.name}:port{port}")

    def if_index(self, port: int) -> int:
        return self.ports[port]


FORWARDING_ROLES = {"core", "distribution", "access", "firewall"}


def learned_via(start: str, links: list[dict], devices: dict) -> dict[str, str]:
    """
    For each other device, which FIRST HOP neighbour does `start` reach
    it through? Returns {device_name: first_hop_neighbour_name}.

    Shortest-path assignment, because a switch learns each MAC on
    exactly ONE port — the one frames actually arrive on. Where the
    topology has a loop, spanning tree picks a single active path, and
    the shortest path is a fair stand-in. Assigning a MAC to every port
    that could theoretically reach it would make every port look like a
    transit port and destroy the leaf/transit distinction inference
    depends on.

    Traversal does not pass THROUGH non-forwarding devices (hosts,
    hypervisors): they terminate frames rather than relaying them.
    """
    adj: dict[str, list[str]] = {}
    for ln in links:
        adj.setdefault(ln["a"], []).append(ln["b"])
        adj.setdefault(ln["b"], []).append(ln["a"])

    first_hop: dict[str, str] = {}
    seen = {start}
    frontier = []
    for nbr in adj.get(start, []):
        if nbr in seen:
            continue
        seen.add(nbr)
        first_hop[nbr] = nbr
        if devices[nbr].role in FORWARDING_ROLES:
            frontier.append(nbr)

    while frontier:
        node = frontier.pop(0)          # FIFO = breadth first = shortest
        for nxt in adj.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            first_hop[nxt] = first_hop[node]
            if devices[nxt].role in FORWARDING_ROLES:
                frontier.append(nxt)
    return first_hop


def build(topology: dict) -> tuple[dict[str, Device], list[dict]]:
    profiles = topology["profiles"]
    devices = {}
    for spec in topology["devices"]:
        prof = profiles[spec["profile"]]
        devices[spec["name"]] = Device(spec, prof)
    return devices, topology["links"]


def render(dev: Device, links: list[dict], devices: dict[str, Device]) -> str:
    rows: list[tuple[str, str, str]] = []

    def add(oid: str, tag: str, val) -> None:
        rows.append((oid, tag, str(val)))

    p = dev.profile
    add(SYS_DESCR, T_OCTET, p["sys_descr"])
    add(SYS_OID, T_OID, p["sys_object_id"].lstrip("."))
    add(SYS_UPTIME, T_TIMETICKS, 8_640_000)
    add(SYS_CONTACT, T_OCTET, "netops@example.local")
    add(SYS_NAME, T_OCTET, dev.name)
    add(SYS_LOCATION, T_OCTET, dev.site)

    add(IF_NUMBER, T_INT, dev.port_count + 1)

    # ifIndex 1 — loopback
    add(f"{IF_INDEX}.1", T_INT, 1)
    add(f"{IF_DESCR}.1", T_OCTET, "lo")
    add(f"{IF_TYPE}.1", T_INT, 24)
    add(f"{IF_MTU}.1", T_INT, 65536)
    add(f"{IF_SPEED}.1", T_GAUGE, 10_000_000)
    add(f"{IF_ADMIN}.1", T_INT, 1)
    add(f"{IF_OPER}.1", T_INT, 1)
    add(f"{IFX_NAME}.1", T_OCTET, "lo")

    # Which ports actually carry a link, so oper status is believable
    linked_ports: set[int] = set()
    for ln in links:
        if ln["a"] == dev.name:
            linked_ports.add(int(ln["a_port"]))
        if ln["b"] == dev.name:
            linked_ports.add(int(ln["b_port"]))

    for port in range(1, dev.port_count + 1):
        idx = dev.if_index(port)
        name = dev.port_name(port)
        up = port in linked_ports
        add(f"{IF_INDEX}.{idx}", T_INT, idx)
        add(f"{IF_DESCR}.{idx}", T_OCTET, name)
        add(f"{IF_TYPE}.{idx}", T_INT, 6)
        add(f"{IF_MTU}.{idx}", T_INT, 1500)
        add(f"{IF_SPEED}.{idx}", T_GAUGE, min(dev.speed_mbps * 1_000_000, 4_294_967_295))
        add(f"{IF_PHYS}.{idx}", T_HEX, mac_hex(dev.port_mac(port)))
        add(f"{IF_ADMIN}.{idx}", T_INT, 1)
        add(f"{IF_OPER}.{idx}", T_INT, 1 if up else 2)
        add(f"{IFX_NAME}.{idx}", T_OCTET, name)
        add(f"{IFX_HISPEED}.{idx}", T_GAUGE, dev.speed_mbps)
        add(f"{IFX_ALIAS}.{idx}", T_OCTET, "" if not up else f"link:{name}")
        base = (idx * 7_919_123) % 4_000_000_000
        add(f"{IFX_HCIN}.{idx}", T_COUNTER64, base)
        add(f"{IFX_HCOUT}.{idx}", T_COUNTER64, base // 3)

    # --- LLDP local ----------------------------------------------------
    if dev.lldp:
        if dev.lldp_quality == "full":
            add(LLDP_LOC_CHASSIS_SUBTYPE, T_INT, 4)      # 4 = macAddress
            add(LLDP_LOC_CHASSIS_ID, T_HEX, mac_hex(dev.chassis_mac))
        # partial: chassis id simply absent, as on RouterOS 6.49
        add(LLDP_LOC_SYSNAME, T_OCTET, dev.name)
        add(LLDP_LOC_SYSDESC, T_OCTET, p["sys_descr"])
        for port in range(1, dev.port_count + 1):
            idx = dev.if_index(port)
            if dev.lldp_quality == "full":
                add(f"{LLDP_LOC_PORT_SUBTYPE}.{idx}", T_INT, 5)   # interfaceName
                add(f"{LLDP_LOC_PORT_ID}.{idx}", T_OCTET, dev.port_name(port))
            else:
                # subtype 3 = macAddress; the NAME lives only in PortDesc
                add(f"{LLDP_LOC_PORT_SUBTYPE}.{idx}", T_INT, 3)
                add(f"{LLDP_LOC_PORT_ID}.{idx}", T_OCTET, dev.port_mac(port).upper())
            add(f"{LLDP_LOC_PORT_DESC}.{idx}", T_OCTET, dev.port_name(port))

    # --- LLDP remote ---------------------------------------------------
    # A neighbour appears only if THIS device runs LLDP, the PEER runs
    # LLDP, no unmanaged switch sits between, and the link's mode allows
    # this direction.
    rem_index = 0
    if dev.lldp:
        for ln in links:
            if ln.get("hidden_switch"):
                continue
            mode = ln.get("lldp", "both")
            if mode == "none":
                continue

            if ln["a"] == dev.name:
                local_port, peer_name, peer_port = int(ln["a_port"]), ln["b"], int(ln["b_port"])
                # mode "one" = only side A advertises, so A sees nothing
                if mode == "one":
                    continue
            elif ln["b"] == dev.name:
                local_port, peer_name, peer_port = int(ln["b_port"]), ln["a"], int(ln["a_port"])
            else:
                continue

            peer = devices[peer_name]
            if not peer.lldp:
                continue

            rem_index += 1
            # Partial implementations do not map neighbours to ports.
            local_idx = dev.if_index(local_port) if dev.lldp_quality == "full" else 0
            key = f"0.{local_idx}.{rem_index}"   # timeMark.localPort.remIndex
            add(f"{LLDP_REM_CHASSIS_SUBTYPE}.{key}", T_INT, 4)
            add(f"{LLDP_REM_CHASSIS_ID}.{key}", T_HEX, mac_hex(peer.chassis_mac))
            add(f"{LLDP_REM_PORT_SUBTYPE}.{key}", T_INT, 5)
            add(f"{LLDP_REM_PORT_ID}.{key}", T_OCTET, peer.port_name(peer_port))
            add(f"{LLDP_REM_PORT_DESC}.{key}", T_OCTET, peer.port_name(peer_port))
            add(f"{LLDP_REM_SYSNAME}.{key}", T_OCTET, peer.name)
            add(f"{LLDP_REM_SYSDESC}.{key}", T_OCTET, peer.profile["sys_descr"])

    # --- Bridge port -> ifIndex map -------------------------------------
    if dev.role in FORWARDING_ROLES:
        for port in range(1, dev.port_count + 1):
            add(f"{DOT1D_BASE_PORT_IFINDEX}.{port}", T_INT, dev.if_index(port))

    # --- Bridge FDB -----------------------------------------------------
    # A port learns every MAC reachable THROUGH it, not just the direct
    # neighbour's. Uplink ports therefore carry many devices and leaf
    # ports carry one — which is what makes strict inference possible
    # on leaves and impossible on transit links.
    if dev.role in FORWARDING_ROLES:
        # Which local port faces each first-hop neighbour?
        port_for_peer: dict[str, int] = {}
        for ln in links:
            if ln["a"] == dev.name:
                port_for_peer[ln["b"]] = int(ln["a_port"])
            elif ln["b"] == dev.name:
                port_for_peer[ln["a"]] = int(ln["b_port"])

        # Which of the DIRECT neighbour's ports faces us? Only that
        # port's MAC is ever learned from it — frames leave a switch on
        # the port that carries them, so the far end's other interface
        # MACs never appear here. Emitting all of them would make the
        # far endpoint ambiguous and force inference to guess a port.
        peer_port_facing_us: dict[str, int] = {}
        for ln in links:
            if ln["a"] == dev.name:
                peer_port_facing_us[ln["b"]] = int(ln["b_port"])
            elif ln["b"] == dev.name:
                peer_port_facing_us[ln["a"]] = int(ln["a_port"])

        fdb: dict[str, int] = {}
        for reached, hop in learned_via(dev.name, links, devices).items():
            bridge_port = port_for_peer.get(hop)
            if bridge_port is None:
                continue
            rd = devices[reached]
            fdb[rd.chassis_mac] = bridge_port
            if reached == hop:
                # direct neighbour: only the port pointed at us
                far = peer_port_facing_us.get(reached)
                if far:
                    fdb[rd.port_mac(far)] = bridge_port
            else:
                # beyond the first hop: any of its ports may source frames
                for rp in range(1, rd.port_count + 1):
                    fdb[rd.port_mac(rp)] = bridge_port

        for mac, bridge_port in sorted(fdb.items()):
            suffix = mac_oid_suffix(mac)
            add(f"{DOT1D_FDB_ADDR}.{suffix}", T_HEX, mac_hex(mac))
            add(f"{DOT1D_FDB_PORT}.{suffix}", T_INT, bridge_port)
            add(f"{DOT1D_FDB_STATUS}.{suffix}", T_INT, 3)   # 3 = learned

    # snmpsim requires OIDs in lexicographic-numeric order
    def sort_key(row):
        return tuple(int(x) for x in row[0].split("."))

    rows.sort(key=sort_key)
    return "\n".join(f"{oid}|{tag}|{val}" for oid, tag, val in rows) + "\n"


# A closed UDP port. snmpsim serves the whole fleet on 1161, so a device
# cannot be made unreachable by removing its data: it is pointed at a port
# with no listener instead, which times out deterministically regardless of
# how snmpsim handles an unknown community. Verified 2026-09-13: the poll
# records reach_status='unreachable', not 'filtered'.
DEAD_PORT = 1162


def reachable_from(start: str, links: list[dict], skip: set[str]) -> set[str]:
    """Devices reachable from `start` over the link graph, ignoring `skip`."""
    if not start or start in skip:
        return set()
    adj: dict[str, set[str]] = {}
    for ln in links:
        a, b = ln["a"], ln["b"]
        if a in skip or b in skip:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen = {start}
    queue = [start]
    while queue:
        cur = queue.pop()
        for nxt in adj.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="sim/topology.yaml")
    ap.add_argument("--out", default="sim/data")
    ap.add_argument("--inventory", default="inventory.generated.yaml")
    ap.add_argument("--down", action="append", default=[],
                    help="simulate this device failing. Repeatable. Anything "
                         "the vantage point can only reach through it goes "
                         "offline with it.")
    ap.add_argument("--vantage", default=None,
                    help="collector vantage point. Defaults to "
                         "collector_vantage in the topology file.")
    ap.add_argument("--expected", default="sim/outage.expected.json")
    args = ap.parse_args()

    topo = yaml.safe_load(Path(args.topology).read_text())
    devices, links = build(topo)

    vantage = args.vantage or topo.get("collector_vantage")
    down = list(dict.fromkeys(args.down))

    unknown = [n for n in down + ([vantage] if vantage else [])
               if n not in devices]
    if unknown:
        print(f"unknown device(s): {sorted(set(unknown))}")
        return 2

    # The fleet answers on one port, so there is no physical cascade. The
    # collateral set is computed here and written out, because the engine
    # under test must recover it from the DISCOVERED graph instead.
    #
    # collateral = reachable before - reachable after - the failed devices.
    # Subtracting what was already unreachable is what keeps a second,
    # disconnected site (the branch) out of every result without needing a
    # vantage point of its own.
    collateral: list[str] = []
    if down:
        if not vantage:
            print("--down needs a vantage point: pass --vantage, or set "
                  "collector_vantage in the topology file.")
            return 2
        before = reachable_from(vantage, links, set())
        after = reachable_from(vantage, links, set(down))
        collateral = sorted((before - after) - set(down))
    offline = set(down) | set(collateral)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.snmprec"):
        old.unlink()

    # Offline devices keep their .snmprec. The box is powered off, not
    # deleted, and nothing polls the dead port.
    for dev in devices.values():
        (outdir / f"{dev.name}.snmprec").write_text(render(dev, links, devices))

    inv = {"devices": [
        {"name": d.name, "host": "127.0.0.1",
         "port": DEAD_PORT if d.name in offline else 1161,
         "version": "2c", "community": d.name, "timeout_s": 2, "retries": 1}
        for d in devices.values()
    ]}
    Path(args.inventory).write_text(
        "# GENERATED by sim/genfleet.py — do not edit by hand.\n"
        + yaml.safe_dump(inv, sort_keys=False))

    # Always written, including the no-outage case. A stale expectation file
    # left over from a previous run would otherwise assert an outage that is
    # no longer staged.
    expected = {
        "topology": args.topology,
        "vantage": vantage,
        "down": down,
        "root_cause": down[0] if len(down) == 1 else None,
        "collateral": collateral,
        "offline": sorted(offline),
    }
    Path(args.expected).write_text(json.dumps(expected, indent=2) + "\n")

    both = sum(1 for l in links if l.get("lldp", "both") == "both"
               and not l.get("hidden_switch"))
    one = sum(1 for l in links if l.get("lldp") == "one")
    none = len(links) - both - one
    print(f"devices : {len(devices)}")
    print(f"links   : {len(links)}  (lldp both={both} one={one} none/hidden={none})")
    print(f"written : {outdir}/*.snmprec")
    print(f"inventory: {args.inventory}")
    print(f"vantage : {vantage}")
    if down:
        print(f"DOWN    : {', '.join(down)}")
        print(f"collateral ({len(collateral)}): "
              f"{', '.join(collateral) if collateral else 'none'}")
    else:
        print("DOWN    : none (full fleet up)")
    print(f"expected: {args.expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
