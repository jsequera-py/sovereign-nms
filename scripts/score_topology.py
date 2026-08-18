#!/usr/bin/env python3
"""
Score discovered topology against sim/topology.yaml.

This is the only honest measurement in the project. Everything else
reports whether code ran; this reports whether the result is true.

Two numbers matter and they fail in opposite directions:

  RECALL    of the links that exist, how many were found?
            Low recall = blind spots. The tool misses real adjacencies.

  PRECISION of the links reported, how many are real?
            Low precision = invented topology. Far worse: a wrong
            dependency edge suppresses alerts for an outage that is
            actually happening.

Precision is the one to protect. A tool that finds 70% of links and is
never wrong is deployable. A tool that finds 95% and invents 5% is not.

Run:
    python scripts/score_topology.py
    python scripts/score_topology.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
import yaml
from psycopg.rows import dict_row


def load_env(path: Path) -> dict[str, str]:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def truth_links(topo: dict) -> dict[tuple[str, str], dict]:
    """
    Ground truth keyed on the unordered device pair.

    Keyed by device pair, not by port pair, because a device-level
    discovery is a partial success and must be scored as such rather
    than as a miss.
    """
    profiles = topo["profiles"]
    by_name = {d["name"]: d for d in topo["devices"]}
    out = {}
    for ln in topo["links"]:
        a, b = ln["a"], ln["b"]
        key = tuple(sorted((a, b)))
        mode = ln.get("lldp", "both")
        if ln.get("hidden_switch"):
            mode = "hidden"

        # Can this link be found by LLDP at all? Requires both ends to
        # run LLDP, no unmanaged switch between, and a mode that emits.
        pa = profiles[by_name[a]["profile"]]
        pb = profiles[by_name[b]["profile"]]
        lldp_possible = (
            pa.get("lldp") and pb.get("lldp")
            and mode in ("both", "one")
            and not ln.get("hidden_switch"))

        # Can it ever be interface-level? Only if the end that reports
        # can say which port — a 'partial' implementation cannot.
        interface_possible = bool(lldp_possible) and not (
            pa.get("lldp_quality") == "partial"
            and pb.get("lldp_quality") == "partial")

        out[key] = {
            "a": a, "b": b,
            "a_port": ln.get("a_port"), "b_port": ln.get("b_port"),
            "mode": mode,
            "lldp_possible": bool(lldp_possible),
            "interface_possible": interface_possible,
        }
    return out


def discovered_links(conn, tenant_id: str) -> dict[tuple[str, str], dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT da.display_name AS a, db.display_name AS b,
                      ia.if_name AS a_port, ib.if_name AS b_port,
                      l.fidelity::text AS fidelity, l.confidence, l.state::text AS state
                 FROM link l
                 JOIN device da ON da.device_id = l.device_a
                 JOIN device db ON db.device_id = l.device_b
            LEFT JOIN interface ia ON ia.interface_id = l.endpoint_a
            LEFT JOIN interface ib ON ib.interface_id = l.endpoint_b
                WHERE l.tenant_id = %s AND l.state = 'active'""",
            (tenant_id,))
        out = {}
        for r in cur.fetchall():
            out[tuple(sorted((r["a"], r["b"])))] = dict(r)
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", default="sim/topology.yaml")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    env = load_env(Path(args.env))
    dsn = os.environ.get("DB_URL") or env.get("DB_URL")
    tenant_id = os.environ.get("TENANT_ID") or env.get("TENANT_ID")
    if not dsn or not tenant_id:
        print("DB_URL and TENANT_ID required", file=sys.stderr)
        return 1

    topo = yaml.safe_load(Path(args.topology).read_text())
    truth = truth_links(topo)

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        found = discovered_links(conn, tenant_id)

    # Only pairs that exist in ground truth are scoreable. A discovered
    # pair involving a device outside the simulation (the real OptiPlex,
    # an eero) is not a false positive — it is out of scope.
    sim_devices = {d["name"] for d in topo["devices"]}
    in_scope = {k: v for k, v in found.items()
                if k[0] in sim_devices and k[1] in sim_devices}
    out_of_scope = len(found) - len(in_scope)

    hits = {k: v for k, v in in_scope.items() if k in truth}
    false_links = {k: v for k, v in in_scope.items() if k not in truth}
    missed = {k: v for k, v in truth.items() if k not in in_scope}

    total = len(truth)
    recall = len(hits) / total if total else 0.0
    precision = len(hits) / len(in_scope) if in_scope else 1.0

    # Of the links LLDP could possibly reveal, how many did we get?
    # This separates "the parser is weak" from "the protocol cannot see
    # it" — two very different problems with different fixes.
    reachable = {k: v for k, v in truth.items() if v["lldp_possible"]}
    reachable_hits = sum(1 for k in reachable if k in hits)
    lldp_recall = reachable_hits / len(reachable) if reachable else 0.0

    # Fidelity correctness: did links that COULD be interface-level
    # actually come out interface-level?
    fid_expected = [k for k, v in truth.items() if v["interface_possible"] and k in hits]
    fid_correct = sum(1 for k in fid_expected if hits[k]["fidelity"] == "interface")

    # Port correctness on interface-level hits — the check that catches
    # a link joined to the wrong ports, which looks right in a count.
    port_checked = port_correct = 0
    for k, v in hits.items():
        if v["fidelity"] != "interface":
            continue
        t = truth[k]
        got = {v["a_port"], v["b_port"]}
        # ground truth stores port NUMBERS; compare on the suffix
        want_nums = {str(t["a_port"]), str(t["b_port"])}
        got_nums = {p.split("/")[-1].lstrip("abcdefghijklmnopqrstuvwxyz")
                    if p else "" for p in got}
        port_checked += 1
        if got_nums == want_nums:
            port_correct += 1

    report = {
        "truth_links": total,
        "discovered_in_scope": len(in_scope),
        "discovered_out_of_scope": out_of_scope,
        "hits": len(hits),
        "missed": len(missed),
        "false_links": len(false_links),
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "lldp_reachable": len(reachable),
        "lldp_recall": round(lldp_recall, 3),
        "fidelity_expected_interface": len(fid_expected),
        "fidelity_correct": fid_correct,
        "port_pairs_checked": port_checked,
        "port_pairs_correct": port_correct,
    }

    if args.json:
        print(json.dumps({"summary": report,
                          "missed": [f"{truth[k]['a']}<->{truth[k]['b']} ({truth[k]['mode']})"
                                     for k in missed],
                          "false": [f"{k[0]}<->{k[1]}" for k in false_links]}, indent=2))
        return 0

    print("=" * 62)
    print("  TOPOLOGY DISCOVERY SCORE")
    print("=" * 62)
    print(f"  ground truth links      : {total}")
    print(f"  discovered (in scope)   : {len(in_scope)}")
    print(f"  correct                 : {len(hits)}")
    print(f"  missed                  : {len(missed)}")
    print(f"  FALSE LINKS             : {len(false_links)}"
          + ("   <-- invented topology" if false_links else ""))
    print()
    print(f"  RECALL                  : {recall:6.1%}  (of all real links)")
    print(f"  PRECISION               : {precision:6.1%}  (of reported links)")
    print()
    print(f"  LLDP-reachable links    : {len(reachable)}")
    print(f"  recall on those         : {lldp_recall:6.1%}  <-- parser quality")
    print(f"  (the rest need FDB/ARP inference, not a better LLDP parser)")
    print()
    print(f"  fidelity correct        : {fid_correct}/{len(fid_expected)} interface-level")
    print(f"  port pairs correct      : {port_correct}/{port_checked}")

    if missed:
        print("\n  MISSED:")
        by_mode: dict[str, list[str]] = {}
        for k in missed:
            by_mode.setdefault(truth[k]["mode"], []).append(f"{k[0]}<->{k[1]}")
        for mode, items in sorted(by_mode.items()):
            note = {"none": "no LLDP either end",
                    "hidden": "unmanaged switch between",
                    "one": "one-sided LLDP",
                    "both": "SHOULD HAVE BEEN FOUND"}.get(mode, "")
            print(f"    [{mode}] {note}")
            for i in items:
                print(f"        {i}")

    if false_links:
        print("\n  FALSE LINKS (fix before anything else):")
        for k in false_links:
            print(f"    {k[0]} <-> {k[1]}")

    print("=" * 62)
    if false_links:
        print("  Precision below 100%. A wrong link becomes a wrong")
        print("  dependency, which suppresses alerts for real outages.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
