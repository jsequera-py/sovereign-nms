# Working method — Sovereign NMS sessions

How this project is worked on. Established 2026-08-20 during Phase 1.2 step 2.
Paste alongside `HANDOFF.md` and `ROADMAP.md` when resuming.

---

## Roles

| Who | Does | Never does |
|---|---|---|
| **The chat session** | Decisions, checkpoints, verification, the durable record | Runs commands on the host |
| **Claude Code on the OptiPlex** | Mechanical execution: file edits, exact text supplied to it | Commits. It stops at `git diff` |
| **Jesus** | Runs commands, pastes raw output, makes the calls | — |

The split exists so every change flows through one reviewed path. Claude Code
has write access to the repo; the chat session does not. Neither has authority
to commit without the other's output.

---

## The loop

1. One **step** at a time: what it is for, one command block, a checkpoint table.
2. Raw output pasted back — not a summary.
3. Output read against the checkpoints. Pass, or stop.
4. Only then the next step.

A step is one change. A read-only inspection may accompany a change; two
changes may not. If two things cannot be verified separately, they are two
steps.

---

## Checkpoints

Every step ends with a table of checks and their pass conditions.

- **Pass conditions are written before the command runs.** Deciding afterwards
  what counts as success is how a failure gets rationalised into a pass.
- **Raw output beats a report.** "All checks passed" is a claim; the terminal
  output is evidence. Ask for the text, not the verdict.
- **`grep -n` over `grep -c`.** Read the lines rather than trust a predicted
  count. `grep -c` counts matching *lines*, not occurrences — a distinction
  that produced three wrong predictions in one session.
- **The chat session states time, dates and machine state only from pasted
  output or from a tool it actually ran.** A session that asserted an unchecked
  clock announced the 24-hour exit-test window open while it was still ~18
  hours away, twice. Acting on it would have counted ~140 rows against a
  540–560 pass condition and read a healthy scheduler as a catastrophic
  failure. `date -u` costs nothing.
- **Check three times if it is worth checking three times.** Cheap.
- **A failed checkpoint stops the sequence.** Diagnose before continuing. This
  is what caught a `snmpwalk | grep -q` + `pipefail` SIGPIPE bug that reported
  a healthy host as broken, before it was committed.
- **Own errors immediately.** Whoever finds a mistake — including their own —
  names it and stops, rather than continuing and hoping.

---

## Commits

- Verify, then commit. Each verified step gets its own commit.
- Every commit closes with `git status --short` clean and both
  `git rev-parse HEAD origin/master` hashes identical.
- The scope check (`git status --short`, `git diff --stat`) comes from git
  directly, never from an agent's summary of what it changed.
- Check the recorded file mode on new scripts: `git ls-files -s` must show
  `100755`. A `100644` script fails on a rebuilt host, at the worst moment.
- Push after every real change. Work that exists only on the OptiPlex has been
  lost before.

---

## Documents

- `HANDOFF.md` in the repo is the single source of truth for state, findings
  and open questions. There is deliberately no second defect log — a third
  home for findings is how findings get lost.
- Project docs are mirrored from the repo after each commit, so a future
  session reads the same state as the machine.
- A handoff that quotes its own HEAD is stale the moment it lands. The commit
  list records the state *before* the docs commit that carries it, and says so.
- **`C:\ARK\NMS\mirror` on the MateBook is downstream and never canonical.**
  Refresh it from the repo, and take the `sha256sum` on the OptiPlex before
  writing any project doc from it. The project copy of `ROADMAP.md` ran 16
  lines behind the repo for three days and nothing surfaced it — a hash
  mismatch found it, by accident.
- **Work authored off the machine reaches the repo by `scp` into `~/nms`,
  then a `sha256sum` match on both sides, then `git add`.** Never by paste,
  never by chat zip. A chat-authored file is legitimate only once its hash is
  published before transfer and matched after; that hash is the whole
  difference between transport and drift. Shipping code through chat zips
  previously cost a hash-by-hand reconciliation and one temporarily lost API.

---

## Standing constraints

- **During a 24-hour exit test**, nothing touches the collector, the sim rig,
  the inventories, or the SNMP daemons. A contaminated result is worth less
  than no result, because a failure becomes unattributable.
- `deploy/` is authoritative; `/etc/systemd/system` is a copy. Verify with
  `install-units.sh --check` and `host-setup.sh --check` — both are read-only
  in check mode and safe while polling is live.
- **Precision over recall.** A missing link is a gap; a false link becomes a
  false dependency, which suppresses a real outage.
- Run the scorer after every collector or generator change, with a reset and a
  re-poll — never against whatever is already in the database.

---

## Voice

Challenge the assumption, name what is missing, say when something is wrong
and why. No agreement openers. Disagree with structure: give the reason and
propose the alternative.
