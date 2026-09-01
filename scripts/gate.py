"""Parses `score_topology.py --json` and asserts seven baseline conditions,
exiting 0 only if all seven hold.

This grades the current database state: it does not reset the database and
does not poll. Running it alone after a code change proves nothing, because
it will report PASS against stale data — this has already happened once in
this project. `make check-scorer` is the sanctioned path, since it resets
and re-polls first.
"""

import argparse
import json
import subprocess
import sys

SCORER_CMD = [".venv/bin/python", "scripts/score_topology.py", "--json"]


def main():
    parser = argparse.ArgumentParser()
    # Scorer rounds recall to three places, so 0.889 is exact equality against
    # a formatting decision in another file; 0.888 still catches any real
    # regression, since the next worse value is 15/18 = 0.833.
    parser.add_argument("--min-recall", type=float, default=0.888)
    parser.add_argument("--min-precision", type=float, default=1.0)
    parser.add_argument("--truth-links", type=int, default=18)
    args = parser.parse_args()

    try:
        proc = subprocess.run(SCORER_CMD, capture_output=True, text=True)
    except OSError as e:
        print(f"FAIL: could not run {' '.join(SCORER_CMD)}: {e}")
        sys.exit(1)

    if proc.returncode != 0:
        print(f"FAIL: {' '.join(SCORER_CMD)} exited {proc.returncode}")
        print(proc.stderr, file=sys.stderr, end="")
        sys.exit(1)

    if not proc.stdout.strip():
        print(f"FAIL: {' '.join(SCORER_CMD)} produced no stdout")
        sys.exit(1)

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(f"FAIL: stdout from {' '.join(SCORER_CMD)} is not valid JSON: {e}")
        sys.exit(1)

    summary = data.get("summary")
    if not isinstance(summary, dict):
        print("FAIL: no 'summary' object in scorer JSON")
        sys.exit(1)

    required_keys = [
        "recall",
        "precision",
        "false_links",
        "port_pairs_correct",
        "port_pairs_checked",
        "lldp_recall",
        "fidelity_correct",
        "fidelity_expected_interface",
        "truth_links",
    ]
    for key in required_keys:
        if key not in summary:
            print(f"FAIL: missing key '{key}' in scorer summary")
            sys.exit(1)

    conditions = [
        (
            "recall",
            summary["recall"] >= args.min_recall,
            summary["recall"],
            f">= {args.min_recall}",
        ),
        (
            "precision",
            summary["precision"] >= args.min_precision,
            summary["precision"],
            f">= {args.min_precision}",
        ),
        (
            "false_links",
            summary["false_links"] == 0,
            summary["false_links"],
            "== 0",
        ),
        (
            "port_pairs_correct",
            summary["port_pairs_correct"] == summary["port_pairs_checked"],
            summary["port_pairs_correct"],
            f"== port_pairs_checked ({summary['port_pairs_checked']})",
        ),
        (
            "lldp_recall",
            summary["lldp_recall"] == 1.0,
            summary["lldp_recall"],
            "== 1.0",
        ),
        (
            "fidelity_correct",
            summary["fidelity_correct"] == summary["fidelity_expected_interface"],
            summary["fidelity_correct"],
            f"== fidelity_expected_interface ({summary['fidelity_expected_interface']})",
        ),
        (
            "truth_links",
            summary["truth_links"] == args.truth_links,
            summary["truth_links"],
            f"== {args.truth_links}",
        ),
    ]

    print("NOTE: scored the current database; no reset or poll was performed by this script.")

    all_pass = True
    for key, ok, actual, expected in conditions:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"{status} {key}: actual={actual} expected {expected}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
