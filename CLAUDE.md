# Working rules for Claude Code in this repo

Authoritative context: `HANDOFF.md` (state, findings, open questions),
`ROADMAP.md` (phases), `working-method.md` (how work flows).

---

## Output is the deliverable

**Your tool results are not visible to the chat session reviewing your
work.** It sees only `Ran N shell commands`. Any output you do not
reproduce in your message body is lost, and a step that cannot be
verified did not happen and must be redone.

For every command you run, your message must contain, in a fenced block:

- the command exactly as run
- its full stdout and stderr, verbatim
- its exit code

Not a description of the output. Not the parts you judged relevant. If a
command printed nothing, say so and show the exit code.

**A step reported without its raw output is void.** Expect to redo it.
"All checks passed" is a claim; the terminal text is evidence.

---

## Never

- **Never run `git add`, `git commit`, `git push` or `git tag`.** Stop at
  `git diff`. Committing is a separate reviewed step run by Jesus. A scope
  check that runs after the commit cannot fail, which defeats its purpose.
- **Never edit `sim/topology.yaml` to make discovery score better.** It is
  the answer key; editing it deletes the only honest measurement here.
  `gate.py` asserts `truth_links == 18` for this reason.
- **Never edit a file after verifying it.** Verification applies to the
  bytes that existed when it ran. A later edit — including formatting,
  comments, or naming — voids it. A further change is a new step with its
  own verification.
- **Never make a change the step did not ask for.** Not formatting, not
  naming, not "while I was in there". If you think something else should
  change, say so and stop.
- **Never merge when told to replace.** A whole-file rewrite means the old
  contents are gone, not interleaved with the new. Truncate first.
- Never batch two changes whose effects cannot be verified separately.
- Never assert a result you did not observe.

---

## Always

- One step, one change. A read-only inspection may accompany it.
- **Report scope from git itself.** End every step with
  `git status --short` and `git diff --stat`, output included. Your own
  account of what you changed is not evidence.
- State pass conditions before running the command, never after. Deciding
  afterwards what counts as success is how a failure gets rationalised
  into a pass.
- Own errors immediately — including your own. Name the mistake and stop,
  rather than continuing and hoping.

---

## Invocation conventions

- **Python under `scripts/` is mode 644 with no shebang**, invoked as
  `.venv/bin/python scripts/x.py`. `/usr/bin/env python3` resolves to
  system python, which has no psycopg; an absolute-path shebang would not
  survive the Phase 6 installer. This is the convention `poll_cycle.sh`
  and both systemd units already use.
- **Shell scripts are mode 755** and run directly.
- `gate.py` runs the scorer from the repo root — `SCORER_CMD` is
  relative. Run it from `~/nms`.
- `grep` here is aliased to ugrep: a pattern starting with `-` needs `--`
  before it. `grep -n` over `grep -c` — read the lines rather than trust
  a predicted count.
- `psql ... -P pager=off`, always. The pager otherwise locks the terminal.
- **Inspect a schema before querying it.** `\d table` first. A guessed
  column name wastes a round trip.

---

## The scorer and the gate

- **After any change to `collector/` or `sim/`, re-run the scorer with a
  reset and a fresh poll** — never against whatever is already in the
  database. Nine bugs have reported success while writing wrong data.
- `scripts/gate.py` turns the baseline into an exit code: recall,
  precision, `false_links`, port pairs, `lldp_recall`, fidelity, and
  `truth_links`. Exit 0 only if all seven hold. The thresholds live there
  and nowhere else — a second copy in a document is how they drift.
- **`gate.py` grades the current database state.** It does not reset and
  does not poll. Run alone after a code change, it prints seven green
  PASS lines against stale data — this has already happened once in this
  project's history. `make check-scorer` is the sanctioned path.
- `reset_data.sh` prompts by default. `--yes` or `RESET_ASSUME_YES=1`
  skips it for automated use. Never pipe `y` into the prompt.

---

## After editing specific paths

- **`server/`** — say so explicitly. `nms-api.service` holds those
  modules in memory, and a test run before the service restart proves
  nothing.
- **`collector/` or `sim/`** — reset, re-poll, re-score. See above.
- **`deploy/`** — `deploy/` is authoritative, `/etc/systemd/system` is a
  copy. Verify with `install-units.sh --check` and `host-setup.sh
  --check`; both are read-only in check mode and safe while polling.

---

## Standing constraints

- **Precision over recall.** A missing link is a gap; a false link
  becomes a false dependency, which suppresses a real outage.
- **Confidence is computed from evidence, never written directly.**
- **A collector reports what it saw, never what it concluded.** Tenant
  and site derive from the collector credential, never from a payload.
- **During a 24-hour exit test**, nothing touches the collector, the sim
  rig, the inventories, or the SNMP daemons. A contaminated result is
  worth less than no result, because a failure becomes unattributable.
- State time, dates and machine state only from output you actually
  observed. `date -u` costs nothing.
