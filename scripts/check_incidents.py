"""
Incident gate: turn "one incident instead of forty alerts" into an exit code.

For each scenario: stage the outage with `sim/genfleet.py --down`, poll the
simulated inventory, run `scripts/incidents.py --json`, and grade the engine
against the expectation genfleet wrote from TRUE topology. The fleet is
restored and re-polled at the end, on success or failure.

HARD conditions (precision; any one fails the gate):
  1. Every root cause the engine names is a device that actually failed.
  2. Every suppressed device was genuinely collateral (or itself failed).
     A false suppression hides a real outage.
  3. With one in-scope failure, the engine names exactly that device as the
     single root of exactly one incident.
  4. A failure outside the vantage's component never appears in an incident.
  5. Expected incident count matches.
  6. No engine error, nothing unplaceable.

MEASURED (recall; floor, not a hard rule):
  coverage = |suppressed AND collateral| / |collateral|, per scenario.
  The known gap is acc-sw-03: discovery never found its links, so the engine
  cannot place it and reports it out of scope. Measured 2026-10-01 at 7/8.

Preconditions: run from the repo root, nms-collector.timer stopped (the
timer polls the same inventory this rewrites), no .exit-test-running.

Run:
    .venv/bin/python scripts/check_incidents.py
    .venv/bin/python scripts/check_incidents.py --corrupt-root core-sw-01
The second must exit 1. A gate never seen to fail is not evidence.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PY = sys.executable
VANTAGE = "srv-mon-01"
EXPECTED = Path("sim/outage.expected.json")

# (label, devices to fail, expected incident count)
SCENARIOS = [
    ("distribution switch",      ["dist-sw-01"], 1),
    ("vantage's access switch",  ["acc-sw-01"], 1),
    ("redundant core, no cascade", ["core-sw-01"], 1),
    ("other site only",          ["br-fw-01"], 0),
    ("two failures, two sites",  ["dist-sw-01", "br-fw-01"], 1),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def stage(down: list[str]) -> None:
    gen = [PY, "sim/genfleet.py"]
    for d in down:
        gen += ["--down", d]
    for cmd in (gen, [PY, "-m", "collector.poll",
                      "--inventory", "inventory.generated.yaml"]):
        r = run(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} exited {r.returncode}\n"
                               f"{r.stdout}{r.stderr}")


def engine() -> dict:
    r = run([PY, "scripts/incidents.py", "--vantage", VANTAGE, "--json"])
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"incidents.py exited {r.returncode}\n{r.stderr}")
    return json.loads(r.stdout)


def grade(label: str, down: list[str], n_expected: int, exp: dict,
          got: dict) -> tuple[list[str], float | None]:
    fails: list[str] = []
    downs, collateral = set(exp["down"]), set(exp["collateral"])
    oos = set(exp.get("out_of_scope", []))

    if got.get("error"):
        fails.append(f"engine error: {got['error']}")
    if got.get("unplaceable"):
        fails.append(f"unplaceable: {got['unplaceable']}")

    roots = [r for inc in got["incidents"] for r in inc["root_cause"]]
    supp = {s for inc in got["incidents"] for s in inc["suppressed"]}
    in_incidents = set(roots) | supp

    bad_roots = sorted(set(roots) - downs)
    if bad_roots:
        fails.append(f"root cause not a failed device: {bad_roots}")
    false_supp = sorted(supp - collateral - downs)
    if false_supp:
        fails.append(f"FALSE SUPPRESSION: {false_supp}")
    leaked = sorted(oos & in_incidents)
    if leaked:
        fails.append(f"out-of-scope failure placed in an incident: {leaked}")
    if len(got["incidents"]) != n_expected:
        fails.append(f"incidents: got {len(got['incidents'])}, "
                     f"expected {n_expected}")
    if exp["root_cause"]:
        hits = [i for i in got["incidents"]
                if i["root_cause"] == [exp["root_cause"]]]
        if len(hits) != 1:
            fails.append(f"root cause: expected exactly one incident rooted "
                         f"at {exp['root_cause']}, got "
                         f"{[i['root_cause'] for i in got['incidents']]}")

    coverage = (len(supp & collateral) / len(collateral)) if collateral else None
    return fails, coverage


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-coverage", type=float, default=0.85)
    ap.add_argument("--corrupt-root", default=None,
                    help="overwrite the first scenario's expected root, to "
                         "prove the gate fails")
    args = ap.parse_args()

    if not Path("sim/topology.yaml").exists():
        print("run from the repo root")
        return 2
    if Path(".exit-test-running").exists():
        print("REFUSED: .exit-test-running exists")
        return 2
    t = run(["systemctl", "is-active", "nms-collector.timer"])
    if t.stdout.strip() == "active":
        print("REFUSED: stop nms-collector.timer first; it polls the "
              "inventory this gate rewrites")
        return 2

    total_fail = 0
    try:
        stage([])
        base = engine()
        if base["incidents"]:
            print(f"FAIL precondition: healthy fleet shows "
                  f"{len(base['incidents'])} incident(s)")
            return 1
        print("PASS precondition: healthy fleet, 0 incidents")

        for n, (label, down, n_inc) in enumerate(SCENARIOS):
            stage(down)
            exp = json.loads(EXPECTED.read_text())
            if n == 0 and args.corrupt_root:
                exp["root_cause"] = args.corrupt_root
            got = engine()
            fails, cov = grade(label, down, n_inc, exp, got)
            if cov is not None and cov < args.min_coverage:
                fails.append(f"coverage {cov:.3f} < {args.min_coverage}")
            cov_s = "n/a" if cov is None else f"{cov:.3f}"
            roots = [i["root_cause"] for i in got["incidents"]]
            supp = sum(len(i["suppressed"]) for i in got["incidents"])
            status = "FAIL" if fails else "PASS"
            print(f"{status} {label}: down={down} incidents={len(roots)} "
                  f"roots={roots} suppressed={supp} coverage={cov_s} "
                  f"out_of_scope={got['out_of_scope']}")
            for f in fails:
                print(f"     - {f}")
            total_fail += bool(fails)
    finally:
        try:
            stage([])
            print("restored: full fleet up, re-polled")
        except Exception as e:  # report, never mask the grade
            print(f"RESTORE FAILED: {e}")
            total_fail += 1

    print(f"result: {total_fail} failure(s) across {len(SCENARIOS)} "
          f"scenarios plus restore")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
