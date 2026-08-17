"""
SNMP acquisition layer.

Design note: both a live device and a recorded .snmpwalk file produce the
SAME list of (oid, type, value) tuples. That is deliberate — it means the
parser is tested against real recorded output, not a mock.

Uses net-snmp binaries via subprocess rather than a Python SNMP library:
  - SNMPv3 auth/priv works correctly without extra dependencies
  - timeouts are enforced at the transport layer
  - recorded walks and live walks share one code path
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# .1.3.6.1.2.1.1.5.0 = STRING: "optiplex"
_LINE = re.compile(r"^(\.[\d.]+)\s*=\s*(?:([A-Za-z0-9\-]+):\s*)?(.*)$")


class SnmpError(Exception):
    """Transport-level failure: unreachable, auth rejected, timeout."""


class SnmpEmpty(Exception):
    """
    Walk succeeded but returned no rows.

    Distinct from SnmpError on purpose. A device with a restricted SNMP
    view answers successfully and returns nothing — it is reachable but
    scoped. Treating that as a failure hides a misconfiguration; treating
    it as success creates a device with zero interfaces.
    """


@dataclass(frozen=True)
class VarBind:
    oid: str          # numeric, leading dot: .1.3.6.1.2.1.1.5.0
    type: str | None  # STRING | INTEGER | Counter64 | Hex-STRING | ...
    value: str        # raw, unquoted


@dataclass
class SnmpTarget:
    """One pollable device. Credentials are per-device, never global."""
    name: str
    host: str
    version: str = "2c"           # "2c" | "3"
    port: int = 161

    # v2c
    community: str | None = None

    # v3
    sec_name: str | None = None
    sec_level: str = "authPriv"   # noAuthNoPriv | authNoPriv | authPriv
    auth_proto: str | None = None  # SHA | SHA-256
    auth_pass: str | None = None
    priv_proto: str | None = None  # AES | AES-256
    priv_pass: str | None = None

    timeout_s: int = 3
    retries: int = 1

    def _auth_args(self) -> list[str]:
        if self.version == "2c":
            if not self.community:
                raise SnmpError(f"{self.name}: v2c requires a community")
            return ["-v", "2c", "-c", self.community]
        if self.version == "3":
            args = ["-v", "3", "-l", self.sec_level, "-u", self.sec_name or ""]
            if self.sec_level in ("authNoPriv", "authPriv"):
                args += ["-a", self.auth_proto or "SHA", "-A", self.auth_pass or ""]
            if self.sec_level == "authPriv":
                args += ["-x", self.priv_proto or "AES", "-X", self.priv_pass or ""]
            return args
        raise SnmpError(f"{self.name}: unsupported version {self.version}")


def _parse_lines(text: str) -> list[VarBind]:
    out: list[VarBind] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        oid, vtype, value = m.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        out.append(VarBind(oid=oid, type=vtype, value=value))
    return out


def walk_live(target: SnmpTarget, root_oid: str) -> list[VarBind]:
    """Walk a real device. Raises SnmpError on transport failure."""
    binary = shutil.which("snmpbulkwalk") or shutil.which("snmpwalk")
    if not binary:
        raise SnmpError("net-snmp not installed (snmpwalk / snmpbulkwalk)")

    cmd = [binary, *target._auth_args(),
           "-ObentU",                      # numeric OIDs, no MIB translation
           "-t", str(target.timeout_s),
           "-r", str(target.retries),
           f"{target.host}:{target.port}", root_oid]

    # Hard ceiling so one dead device cannot stall the whole poll cycle.
    budget = target.timeout_s * (target.retries + 1) + 10
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=budget)
    except subprocess.TimeoutExpired as exc:
        raise SnmpError(f"{target.name}: exceeded {budget}s walking {root_oid}") from exc

    combined = (proc.stdout or "") + (proc.stderr or "")
    low = combined.lower()
    for marker in ("timeout: no response", "authentication failure",
                   "unknown user name", "no such object", "no such instance",
                   "usmstatsnotintimewindows"):
        if marker in low and not proc.stdout.strip():
            raise SnmpError(f"{target.name}: {marker} at {root_oid}")

    if proc.returncode != 0 and not proc.stdout.strip():
        raise SnmpError(f"{target.name}: snmpwalk rc={proc.returncode} {proc.stderr.strip()[:200]}")

    binds = _parse_lines(proc.stdout)
    if not binds:
        raise SnmpEmpty(f"{target.name}: {root_oid} returned no rows")
    return binds


def walk_file(path: str | Path, root_oid: str) -> list[VarBind]:
    """Replay a recorded walk. Same return shape as walk_live."""
    text = Path(path).read_text(errors="replace")
    binds = [vb for vb in _parse_lines(text) if vb.oid.startswith(root_oid)]
    if not binds:
        raise SnmpEmpty(f"{path}: {root_oid} returned no rows")
    return binds


def walk(target: SnmpTarget, root_oid: str,
         recorded: str | Path | None = None) -> list[VarBind]:
    """Single entry point. Set `recorded` to replay instead of polling."""
    if recorded:
        return walk_file(recorded, root_oid)
    return walk_live(target, root_oid)
