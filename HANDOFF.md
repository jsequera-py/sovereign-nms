# Sovereign NMS — Session Handoff

Paste this into a new chat, along with `ROADMAP.md`, to resume.

Last updated 2026-08-26. State below was verified on the machine, not assumed.

---

## START HERE

### Verified state (2026-08-26)

Most recent commits at the time of writing. The docs commit that carries this
file sits on top of them, so HEAD is one ahead of this list by design — do not
"correct" it.

```
a88e91e scripts: check_identity.py — detect the silent device merge, with a selftest
a6bf57b docs: close phase 1.2 steps 3 and 4 with measured evidence
b867e68 docs: scorer collapses same-pair links; close the dedup-fix question
4c0213d docs: record the confidence formula, retire the interface-design §8 workaround
8464e57 docs: settle open question 1 — stale links keep their confidence
5c7b3fa topology: a stale link keeps its last confidence, loses only its authority
052db27 docs: close open question 10 with the measured result
a3ec23c ingest: clock_timestamp for finished_at, so run duration is measurable
513a18d docs: CLAUDE.md — bind the working method to the on-machine agent
```

Working tree clean, local and origin in sync. `migrations/` holds 001–006, and
`schema_migration` records 006 applied 2026-08-24 23:39 UTC. `deploy/` holds
`nms-api.service`, `nms-collector.service` and `nms-collector.timer`; the
installed copies under `/etc/systemd/system` were verified byte-identical to
the repo copies. `nms-db` and `nms-snmpsim` up.

**`nms-api` was restarted after `ecc4362`, so it runs current ingest code.**
It holds `server.ingest` in memory: editing that module changes nothing until
the service is restarted, and a test run before the restart proves nothing.

Host clock is NTP-synchronised (chrony, sub-millisecond offset). Host, RTC
and Postgres all run UTC, deliberately — the database stores UTC, and
scenario 2 puts collectors in other timezones. Read local time with
`TZ=America/Mexico_City journalctl ...` rather than changing the host.

**`nms-collector.timer` is installed, enabled and running.** Unattended
polling began **2026-08-20 20:40 UTC**; observed spacing ~5m20s. Both fleets
are in the database — 14 simulated devices and **2** real targets — and both
are re-polled. See **The scheduler** below.

**Data state, which affects any query run against this database.**
`reset_data.sh` was run 2026-08-24 ~03:00 UTC, so all metric history starts
there. `rb951g-lab` was rebooted 2026-08-25 00:31 UTC for the 1.3 exit test,
so its `sysUpTime` is small and its counters restarted. `interface_rate`
carries ~44 `counter_decrease` and ~72 `no_time_base` rows, all predating
`bd1dc50`, all suppressed — permanent debris from the replay double-write,
not a live fault.

**`optiplex-replay` is no longer scheduled** (`bd1dc50`). It lives in
`inventory.replay.yaml` and is run by hand only; a manual run injects one junk
counter sample for `lo`, `eno2` and `wlo1`.

### Next: Phase 2 — dependency direction

Phases 1.2 and 1.3 are both closed. 1.2 scored 5/6 on its 24-hour exit test
(full record at the bottom of this file); two of its sub-tests remain
deliberately unrun — the overlap test and the wedged-run test, see **The
scheduler**. 1.3 passed 2026-08-25.

**1.3 result.** Rates and utilisation are computed on the *read* path by the
`interface_rate` view (migration 006, `e85dd81`) from raw counters — nothing
derived is stored. The time base is `sysUpTime`, persisted per device as a
`metric_sample` row (`ecc4362`), because the device's own clock keeps
collector delay, POST latency and NTP skew out of the denominator.
Verified by rebooting `rb951g-lab` at 2026-08-25 00:31 UTC: the sample pair
spanning the reboot reported `uptime_reset` on all 14 rows with `rate_bps`
null. `ether1-gateway` showed `delta_octets = -5,294,865,861`, which unguarded
renders as a -230 Mbps reading — or as a fabricated 230 Mbps burst if someone
"fixes" it with `abs()`. Idle ports report `uptime_reset` too, not a
comfortable zero: no rate is trustworthy across that boundary, including a
zero one.

**Three caveats the pass does not cover.**

1. The `counter_decrease` branch — counters drop while uptime does not — was
   never freshly reproduced. See the RouterOS finding under **Vendor
   realities**. Its only evidence is 44 rows of replay debris predating
   `bd1dc50`: real data, accidentally generated.
2. **The simulated fleet cannot test any of this.** Its `sysUpTime` is frozen
   by the static `.snmprec` files, so it never exercises the uptime time base
   or the reboot path. Measured: one distinct uptime value across 218 samples
   per simulated device. Only real hardware can grade this.
3. `interface_rate` filtered by interface runs in 38 ms. Filtered only by time
   it is 221 ms at ~101k rows, and it scales with the whole table rather than
   the range asked for — the `ts` predicate cannot be pushed past `lag()`
   without removing the row the window needs. **Phase 5's fleet-wide view must
   not read it directly;** that screen needs a continuous aggregate.

**Before Phase 2, implement the identity veto rule.** Direction inference
leans hard on device rows being right.

### Session record — 2026-08-26

A review session. Nothing was built toward Phase 2; the work was making the
record match the machine and closing claims that were asserted rather than
measured.

**Code changed, both verified on the machine.**

- `a3ec23c` — `finished_at = clock_timestamp()`. `now()` is
  `transaction_timestamp()` and the connection runs `autocommit=False`, so
  every `discovery_run` row carried `started_at == finished_at` to the
  microsecond. Post-fix rows read 0.562 s and 0.058 s. Open question 10.
- `5c7b3fa` — the rollup no longer zeroes `confidence` on a stale link.
  Verified by ageing one link's evidence: `active → stale`, 0.95 intact.
  Full regression run first; scorer unchanged. Open question 1.
- `a88e91e` — `scripts/check_identity.py`, with a `--selftest` that injects a
  synthetic duplicate chassis_id and rolls it back. First run: 18 devices,
  0 violations, selftest fires.

**Claims closed with evidence rather than assertion.** Phase 1.2 steps 3 and
4 — overlap tested on the real unit, wedged run on a replica; see **The
scheduler**. And the `dedup-fix` scorer question, open since 2026-08-19, now
open question 12.

**Documentation corrected.** `ROADMAP.md` claimed 4 migrations with 6
applied, and omitted two tables and a view. The 4TB retention conclusion was
wrong by roughly 6× — 630 GB/year is about six years on a 4TB NVMe, not one.
The evidence model documented two numbers as though they were the rule; the
rule is a formula. `CLAUDE.md` now binds the on-machine agent to this working
method, and caught its first violation within one step.

**Project and mirror.** `C:\ARK\NMS\mirror` is a real git clone now, so
refreshing it is `git pull` rather than a hand copy verified by hash. The
claude.ai project docs were resynced from the repo and de-duplicated — a
stale `ROADMAP.md` from 2026-08-18 was being served alongside the current
one, still claiming 4 migrations and the retired two-poll story.

### Session record — 2026-09-01

A state-reconciliation session. Nothing was built toward Phase 2; the work was
verifying the machine against the record, and turning the scorer baseline from
a number in a document into an exit code.

**Code changed, all verified and pushed.**

- `6f139e8` — `check_identity.py` no longer carries a shebang and is mode 644.
  `/usr/bin/env python3` resolves to system python, which has no psycopg, so
  a direct `./scripts/check_identity.py` died on import. All Python under
  `scripts/` is now invoked as `.venv/bin/python scripts/x.py` — the
  convention `poll_cycle.sh` and both units already used.
- `eb22c93` — `reset_data.sh --yes` (also `RESET_ASSUME_YES=1`) for
  non-interactive use. The prompt stays the default; piping `y` would make
  destruction reflexive. The `[ test ] && VAR=1` idiom was verified safe under
  `set -e` on both branches.
- `be214f2` — `scripts/gate.py`. The scorer prints and always exits 0, so the
  baseline lived only in markdown and in whoever read the terminal. The gate
  parses `--json` and asserts seven conditions, four never previously
  enforced: port pairs, `lldp_recall`, fidelity, and `truth_links`. Verified
  failing on `--min-recall 0.95` and `--truth-links 99`, exit 1 each time with
  the other six still passing.
- `4b8c8a1` — `CLAUDE.md` rewritten. See below.
- `428d8fe` — the next-session sequence, corrected by this audit.

**What the audit found.**

- **Baseline device count is 19 / 15 hard identities, not 18 / 14.** The delta
  is the MateBook dock, `74bc10bd`, `first_seen 2026-08-28`, carrying one
  `chassis_id` at 0.8. Nothing unaccounted for.
- **Both eero placeholders carry a `chassis_id`** and neither carries a
  `sysname` row. This closes step 1 of the old next-session plan and collapses
  steps 2 and 3 into one action.
- **Open question 12 is closed** by `b867e68`. Reference hash for the live
  scorer: `4b46a9dd217ba44b862cbab549803514334b2306ab62c9f1c76c341b7daa694c`.
- **Open question 9's mechanism is visible in data.** Exactly one
  `mgmt_ip / 127.0.0.1 / inferred` row exists — the unique constraint permits
  only one — and it is reassigned between devices every cycle.
- **The simulated switches carry 24–26 `base_mac` identity rows each.** If
  `base_mac` resolves, each device offers ~25 independent chances of a
  cross-device collision. The veto rule must be sized against that population.
- Migrations 001–006 confirmed applied; `schema_migration` keys on `filename`,
  not a version integer. 14 tables, three of which (`site`, `tenant`,
  `schema_migration`) are absent from `ROADMAP.md`'s list.

**The accidental wipe.** `reset_data.sh` was run inside a verification block
where the pass condition was "answer `n`, nothing deleted", and `y` was
entered. 713,262 `metric_sample` rows and 3,243 `discovery_run` rows were
lost, including the evidence base of the Phase 1.2 exit test — that record
survives in this file, so no claim is weakened. `tenant` and `collector_key`
were never in the delete list.

**What it proved.** One poll cycle reproduced the baseline exactly: 0.889
recall, 1.0 precision, 16/16 port pairs, 0 false links, both named misses
unchanged. The destructive path is now validated rather than assumed, which is
precisely the assertion `make check-scorer` will make. It also proved that
**an interactive prompt is not a safety mechanism** — it fired correctly and
the database still went. `check-scorer` needs the `.exit-test-running`
lockfile, not a prompt.

**Why `CLAUDE.md` was rewritten.** Three violations by the on-machine agent in
one session, all of the same rule. The cause was not defiance: the agent's
tool results are collapsed to `Ran N shell commands` before the reviewing
session sees them, so it believed it had reported faithfully. The old rule
— "quote the command output" — was also satisfiable by a fragment. The new
file names the mechanism, requires stdout, stderr and exit code for every
command, and declares a step reported without them void. It also forbids
editing a file after verifying it, and merging when told to replace: a
whole-file `Write` against `CLAUDE.md` itself interleaved old and new
contents, producing a rules file that contradicted itself in three places.
The replacement was written by heredoc instead.

**Deferred, deliberately.** `gate.py`'s four input-failure branches — non-zero
exit, empty stdout, invalid JSON, missing key — are written and read but
unexercised. Proving them means temporarily breaking the scorer, which is a
change to a file under test, so it is a separate step. Each branch is three
lines and exits immediately.

### Session record — 2026-09-12

The gate closed, the topology became visible, and a loose cable found a real
defect. Five commits.

- `0962f1e` — `working-method.md`: compare mirror files with `git hash-object`,
  not `sha256sum`. The mirror has `core.autocrlf=true`, so a byte hash
  mismatches on every text file and the check could never pass. It appears
  never to have run. Verified the same day: all three docs mismatched on
  `sha256sum` and matched exactly on `git hash-object`.
- `3d3ec1b` — `Makefile`. `make check` is read-only and runs in 0.5s with a
  warm sudo timestamp: identity, its selftest, a clean-tree assertion, unit
  and host drift. `make check-scorer` is destructive and runs in 7–9s:
  `reset_data.sh --yes`, `poll_cycle.sh`, `gate.py`. It calls `poll_cycle.sh`
  rather than re-implementing the two polls. It refuses while
  `.exit-test-running` exists — verified locked, refusing at the first block
  with the device count unchanged. Default target is `help` and does nothing.
  **`make` is not installed by default on Ubuntu 26.04**; 4.4.1-3 was installed
  on the OptiPlex and belongs in the host-configuration list.
- `0c9d5e3` — `web/topology.html`, mounted at `/ui` by the API. One file, no
  CDN, so it works air-gapped. Mounted after all `/v1/*` routes.
- `dd0e8b9` — viewer: port labels on hover, grouping by connected component.
- `0938d11` — the reachability history row records the resolved device.

**The gate is closed.** `gate.py` existed but nothing ran it with a reset,
which is the false-pass path its own docstring warns about. `make check-scorer`
now does reset → poll → grade → exit code. The 88.9% / 100% baseline is no
longer a number in a document that someone has to remember to check.

**What the graph showed that a percentage did not.** `acc-sw-03` renders as an
isolated dot with no links, because its only two links are the scorer's only
two misses. "88.9% recall" and "one device is entirely absent from the
network" are the same fact, and the number had been hiding it for three weeks.
The simulated fleet is also three components, not one — core (10), branch (3),
and `acc-sw-03` alone — which is why the viewer's first grouping heuristic,
"largest component is the simulated fleet", was false and was replaced with
plain connected components. Nothing in `/v1/topology` distinguishes simulated
from real, and `mgmt_ip` would only work on this desk.

**The loose cable.** At 20:36 UTC the OptiPlex's ethernet came loose at the
eero. The collector kept running for 2h07m: the simulated fleet polled 14/14
throughout, `rb951g-lab` was recorded `unreachable` with the exact error
(`snmpbulkwalk: Failure in sendto (Network is unreachable)`), one transition
row rather than one per cycle, and the run still completed and reported. The
two inventories are independent and a real-hardware failure did not contaminate
the sim rig. A collector reported what it saw and concluded nothing — the
non-negotiable, exercised by an actual fault rather than a designed test.

**The defect it found.** `device_reachability_change` rows for transitions INTO
unreachable were written with a null `device_id`. A failed poll carries no
device_id; the state upsert compensated with COALESCE, the history insert
passed the raw parameter. So the table could answer when a device came back and
not when it went down — backwards for the incident timeline Phase 3 will read
from it. Fixed with `RETURNING device_id` on the upsert. Verified by blocking
udp/161 to the MikroTik and forcing the transition: the new row carries the
device, the row from three minutes earlier does not.

**`record_reachability` runs in the API process, not the collector.** It lives
in `collector/store.py` but its only callers are in `server/ingest.py`. The
first fix attempt looked like a failure for four minutes because
`nms-api.service` still held the old module in memory. **The CLAUDE.md restart
rule names `server/`, but the hazard is about what the service imports** — a
file under `collector/` can need an API restart. The rule should be reworded.

**Also worth knowing.**

- `systemctl restart` returns before uvicorn binds. A curl issued immediately
  after gets `000` and looks like a crash. Startup is under a second.
- A reset writes a reachability transition for every device at once, so history
  will show a mass event at each `check-scorer`. Not wrong, but it will look
  like an outage in any future chart.
- Two orphaned history rows from before the fix were left as they were, then
  cleared by the next reset. Orphan count should stay at 0; if it grows, the
  fix regressed.
- The eero mesh does not forward LLDP, which is why `optiplex` and
  `rb951g-lab` have never appeared as neighbours despite being two hops apart.
- The OptiPlex is a single point of failure for the database, the API, the
  collector and the UI, and it sits behind a consumer mesh on a segment the
  MateBook reaches only by routing through the MikroTik.
- **The gap between the fault at 20:36 and noticing it was an SSH timeout at
  22:45.** The system knew immediately. Nothing told anyone. That is Phase 3,
  demonstrated by accident on real hardware.

### Session record — 2026-09-13

An experiment, not a build. A Raspberry Pi 4 was connected to the lab to
observe what discovery does with an unknown device. Run as a negative
control first, then a positive test, with pass conditions written before
each command.

**The device.** Raspberry Pi 4, `DC:A6:32:EE:99:A9`, RetroPie on Raspbian
10 buster, hostname `retropie`, MikroTik bridge port 4, DHCP lease
`192.168.88.253`.

**Negative control: a bare unmanaged device is invisible, correctly.**
With no `lldpd` and no `snmpd`, the Pi was learned in the MikroTik FDB
(`dot1qTpFdbPort` port 4, `dot1dTpFdbStatus` 3) and held a DHCP lease.
Across 8 poll cycles the NMS produced zero rows referencing it: 19 devices,
19 links, nothing new since T0, no `device_identity` row in any encoding.
The stimulus was proven delivered before the null result was accepted.

**Why, and the general rule it establishes.** `find_leaf_ports` qualifies a
port only when its single learned MAC maps to an existing device
(`collector/fdb.py:158`). An unknown MAC has no owner, so no link, and
`mac_table` never creates devices. **`mac_table` cannot discover anything;
it can only corroborate what LLDP already found.** The 88.9% recall is
entirely LLDP-driven, and the two misses are both non-LLDP links that no
volume of FDB data reaches under this rule. Confirmed from the opposite
direction on hardware.

**FDB self-filtering verified on hardware.** `fdb.py` reads `FDB_STATUS`
and `learned()` excludes self(4) and mgmt(5). The walk agrees: dock and Pi
both `3`, both `E4:8D:8C` Routerboard addresses `4`. The strictness that
buys 100% precision is earned, not accidental.

**Positive test.** `lldpd 1.0.3-1+deb10u2` installed on the Pi, nothing
else. Result at the next cycle, every prediction met: one device
`display_name = retropie`, vendor NULL, state `unpolled`; one link
`rb951g-lab <-> retropie`, **device** fidelity, confidence **0.80** from
`lldp` 0.65 plus one independent `mac_table` confirmation; 20 devices, 20
links. **Scorer unchanged at 88.9% / 100%, 16/16 port pairs, 0 false
links** — real hardware outside `sim/topology.yaml` did not move the
simulated answer key.

Named `retropie` rather than by MAC because `retropie` is not in
`GENERIC_SYSNAMES`, so the sysName claim survived the filter. It resolves
on two identifiers where the eeros resolve on one.

**Device fidelity here is a vendor limit, not a design limit.** The
evidence row reads `"peer_if": "eth0", "reporter_if": null`. The Pi's port
is known; the MikroTik cannot name its own because
`lldpRemLocalPortNum = 0`. This link is one vendor behaviour away from
interface fidelity.

**Decision taken.** Device adoption is scope-approved, not device-approved.
Recorded in ROADMAP 6.5 and in Architectural decisions.

**Environment note.** `raspbian.raspberrypi.org` no longer serves a buster
Release file; the archive moved to `legacy.raspbian.org`. Any buster-era
Pi in this lab needs its `sources.list` repointed before `apt` works.

**Decay verified on hardware that actually left.** The Pi was unplugged at
2026-09-13 00:52 UTC. After the evidence aged past the 30-minute freshness
window the rollup marked the link `stale` with **confidence intact at
0.80**, link states 19 active / 1 stale. `5c7b3fa` had previously been
verified only by artificially ageing one link's evidence; this is the
first time a real device left and the read path took the last-known number
straight from `link.confidence`. The old code would have written `0.00`.

### Next session — in order

**Step 1 of the previous plan is answered.** Both eero placeholders carry a
`chassis_id` claim at confidence 0.8 (`0c:93:a5:24:86:e0` and
`30:34:22:d7:1b:00`; device UUIDs are not quoted because a reset regenerates
them), and neither carries a `sysname` identity
row, because `eero` is in `GENERIC_SYSNAMES` and the claim was never written.
The redesign risk is retired: dropping `eero` from the denylist makes both
assert `sysname = eero`, the second resolution merges onto the first, and the
merged row then holds two chassis_ids — exactly the signature
`check_identity.py` detects. **Old steps 2 and 3 collapse into one action.**

1. Drop `eero` from `GENERIC_SYSNAMES`, reset, re-poll. **`check_identity.py`
   must report a violation.** That edit is temporary and must never be
   committed: `git checkout -- collector/store.py` and a clean `git status`
   before moving on. Note `record_reachability` lives in the same file and
   runs in the API process — `sudo systemctl restart nms-api.service` after
   any edit here, or the running service keeps the old module.
2. Implement the unmatched-identifier veto in `resolve_device()`. Reset,
   re-poll with `eero` still absent from the denylist. The check must come
   back clean — proving the veto did the work, not the denylist.
3. Restore `eero`, then `make check-scorer`. Green is the gate now; no
   separate reset or scorer invocation needed.
4. **Phase 2.0 — direction ground truth.** `ROADMAP.md` orders 2.1 (inference)
   before 2.2 (the scorer that grades it), which inverts the discipline that
   produced every honest number here. Add upstream/downstream labels to
   `sim/topology.yaml`, extend the scorer to read them, and confirm it reports
   0% direction accuracy against an empty `dependency` table. A scorer that
   can fail before anything exists to grade is one worth trusting. Extend
   `gate.py` with the direction condition once it produces a number.
5. Then 2.1 inference, then 2.3 threshold calibration.

**Smaller items, any time.**

- **Reword the CLAUDE.md restart rule.** It names `server/`, but the hazard is
  what `nms-api.service` imports. `collector/store.py` needed an API restart
  and the rule did not say so.
- `make check` ordering: the clean-tree assertion runs third, after both
  identity checks. On a dirty tree it wastes them. Cheap to move first.
- Record `make` (4.4.1-3) in the host-configuration section — a rebuilt host
  gets a Makefile it cannot run.
- `/ui/` 404s: `html=True` wants `index.html` and the file is `topology.html`.
  Decide whether topology is the UI's front door before renaming.
- The viewer prompts for the collector bearer token. Replace it when the read
  credential in `interface-design` question 4 exists, and rotate that token —
  it was pasted into a chat transcript on 2026-09-12.

**Sizing note for the veto.** The simulated switches carry 24–26 `base_mac`
identity rows each. If `base_mac` resolves, every device offers ~25
independent chances of a cross-device collision. Size the veto against that
population deliberately rather than discovering it afterwards.

**Also open, not blocking.**

- The sim rig cannot express a shared sysName. `genfleet.py` writes `sysName`
  from the device name and seeds `chassis_mac` from that same string, so two
  devices sharing a name would also share a chassis MAC. A `sysname:` field
  decoupled from `name` is needed first.
- `sysUpTime` is a single constant in the generator, so the simulated fleet
  cannot exercise the 1.3 reboot path.
- All simulated devices share `mgmt_ip = 127.0.0.1` because snmpsim binds one
  port. Distinct loopback addresses would cut ~4,000 journal lines a day —
  but one pair should keep a shared address deliberately, as the standing
  proof that `mgmt_ip` cannot resolve identity.
- Four legacy PDFs in the claude.ai project still describe the abandoned
  architecture.
- The RouterOS API and Scripting manuals in the project have no stated use;
  collection is SNMP-only throughout the roadmap.

---

## What this is

A network monitoring system whose differentiator is **automatic,
continuously-rebuilt topology discovery** feeding **alert suppression with
root cause**. Deployment scenarios 1 (single-node on-prem) and 2 (distributed
on-prem) only.

### The pitch (the "soul" — everything is judged against it)

> Your monitoring tool knows your devices. It doesn't know your network.
> When a switch fails at 2am, it sends you forty alerts instead of one,
> because nobody has kept the dependency map current since 2023.
>
> We discover the topology and rebuild it continuously — no manual
> dependency configuration. So when something breaks, you get root
> cause, not alert spam. And you can ask in plain language, with the
> answer grounded in what we actually observed on the wire.

---

## Roadmap position

| Phase | State |
|---|---|
| 1.1 Collector ingest client | **done** |
| 1.2 Scheduler — step 1, reachability persisted | **done** |
| 1.2 Scheduler — step 2, the timer | **done** |
| 1.2 Scheduler — 24h exit test | **passed 5/6, 2026-08-22** — exception recorded |
| 1.3 Counter deltas | **done 2026-08-25** — read-path rates, `interface_rate` |
| 1.4 Real device in the pipeline | **done** |
| 2 Dependency direction | not started — settle identity design first |
| 3 Alerting and suppression | not started — this is the sellable demo |
| 4 Syslog and the AI layer | not started |
| 5 Interface | not started — IA and read contract drafted, `interface-design.md` |
| 6 Deployable product | not started |

| Metric (simulated fleet) | Value |
|---|---|
| Recall | 88.9% (16/18 links) |
| **Precision** | **100%** |
| Port pairs correct | 16/16 |
| Recall on LLDP-reachable links | 100% |
| `auto_edge_pct` | 100.0 |

**This baseline must not regress.** Run the scorer after every change to the
collector or the fleet generator — with a reset and a re-poll, not against
whatever is already in the database.

---

## Open finding: topology does not decay, and that is a problem

**Write-path half answered 2026-08-22 by the exit test.** With the timer
running, `rollup_confidence()` *does* drive a link to `stale`:
`rb951g-lab ↔ 64:c9:01:a9:42:7e` went stale after its evidence aged out at
2026-08-21 13:30 UTC. Decay works when the rollup runs — what was broken was
that nothing ran it. The measurement below describes an unpolled system, not
this one.

**But the rollup zeroes `confidence`.** That link read `0.80` (lldp +
mac_table) and now reads `0`. `interface-design.md` §4 requires a stale link to
keep its last confidence and lose only its authority — `0.80 · unverified 30h`,
never a decayed number. The write path does the opposite. Two consequences: the
§4 render cannot read `link.confidence`, and a Phase 2 threshold test against
`dependency_trusted = 0.80` reads a stale link as maximally untrustworthy
rather than unverified — a different claim, and one nobody decided to make.

**Recoverable, and cheaply.** `link_evidence` keeps both rows with
`observed_at` and `raw_claim` intact, so last-known confidence is computable on
the read path with no schema change and nothing owed by Phase 3. What stays
open is narrower: whether `link.confidence` should be zeroed at all. Decide
before Phase 2.3 calibrates the threshold.

Measured 2026-08-20, before the scheduler existed. Last poll **776 minutes**
earlier — 13 hours, 25× the 30-minute `link_evidence_fresh` window. The
scorer still reported **88.9% / 100%** with 16 links `active`.

Evidence ages, but nothing marks it aged until a poll runs and
`rollup_confidence()` re-evaluates. No poll, no rollup; no rollup, links stay
`active` at their last confidence indefinitely.

**A collector that dies leaves a topology that looks perfectly healthy
forever.** `/v1/health.minutes_since_last_run` is the only signal, and nothing
surfaces it.

This has not been confirmed against the code — it is inferred from observed
behaviour. **Settle it before Phase 5**, because a topology view rendered from
13-hour-old data would look identical to a live one.

Questions to answer:

1. Does `rollup_confidence()` change `link.state`, or only confidence?
2. Should staleness be a property the *read* path computes (`max(evidence
   age)` per link), rather than something only a write pass can discover?
3. Should `/v1/topology` refuse — or flag — results when
   `minutes_since_last_run` exceeds the freshness window?

Option 2 is probably right: staleness is a function of time, and time passes
whether or not anything polls.

---

## Identity design — decided, not yet implemented

Full note in `identity-design.md`. Summary:

**The hole.** Two eeros both advertise `SysName: eero`. Device B arrives with
an unseen `chassis_id` and the shared sysName. The chassis matches nothing,
the sysName matches device A, so `matched` has exactly **one** entry — and the
"manual merge required" warning needs two. B is absorbed into A silently. The
reassignment warning does not fire either, because the sysName never moved.
All three existing safeguards miss it.

**The fix — an unmatched identifier vetoes a weaker matched one.** If the
claim set contains a resolving identifier that matches nothing and it outranks
the strongest one that did match, this is new hardware: create a new device.
A chassis ID nobody has seen means a box nobody has seen, whatever its name
claims.

Local, no new state, decides *before* the merge. It supersedes the earlier
"learn generic names over time" idea, which fired too late, needed a history
table, and would have punished the legitimate keep-the-name-after-an-RMA case.

Careful with one case: interface churn produces unmatched `base_mac` claims
routinely. *Strictly* stronger does the work — an unmatched `base_mac` never
outranks a matched `base_mac`.

`GENERIC_SYSNAMES` stays as the floor for devices whose only claim is a
product name.

**Second hole found in the same read:** identity resolution is tenant-scoped,
not site-scoped. Two sites in one tenant each with a `core-sw-01` resolve to
one device — the shared-`mgmt_ip` bug by another road, landing squarely on
scenario 2. Strong identifiers (`serial`, `chassis_id`, `base_mac`) should
stay tenant-wide; `sysname` should resolve within a site only.
`cross_site_links` in `/v1/health` exists to catch this but has never been
exercised, because there is one site.

**Test-rig gap:** nothing in `sim/topology.yaml` has two devices sharing a
sysName, so the scorer cannot catch a silent merge. That scenario exists only
in the real lab, where nothing grades it. Add it.

---

## Environment

**Hardware**
- OptiPlex 5060, Ubuntu 26.04, `192.168.4.181`, user `jsequera`
- MateBook D14 — thin client only, VS Code Remote-SSH into the OptiPlex
- MikroTik RB951G-2HnD — RouterOS **6.49.20**

**Confirmed lab topology**

```
ISP modem ── eero GTW ──(wired eno2)── OptiPlex        192.168.4.181
             30:34:22:d7:1b:00                          (eero GTW mgmt 192.168.4.1)
                 ))) wireless mesh )))
             eero 3 AP     eero 2 AP ──(wired ether1)── MikroTik  192.168.4.182
                           0c:93:a5:24:86:e0             bridge   192.168.88.1
                                                             └─(wired)── MateBook dock
                                                                  64:c9:01:a9:42:7e
                                                                  192.168.88.254
```

The eero mesh does **not** forward LLDP across the wireless hop. Confirmed by
absence: no phantom `optiplex ↔ rb951g-lab` link was ever created.

**MikroTik details**
- ether1 (WAN, to eero 2 AP): `192.168.4.182` · bridge-local: `192.168.88.1`
- SNMP community: `nmslab` · LLDP enabled, discovery interface list = all
- Firewall: `input accept` for src `192.168.4.181`, udp/161, in-interface
  ether1, **above** the default WAN drop rule
- wlan1 disabled
- **System Identity is `rb951g-lab`** (was the factory default `MikroTik`,
  now refused as an identifier)

**Not usable for monitoring:** eero mesh (cloud-managed, no SNMP — but eeros
advertise LLDP, so they appear as neighbours), Cisco X1000 DSL modem, Luxul
XGS-1008 (unmanaged — useful only as a physical "hidden switch" test).

**Stack**
- Postgres 16 + TimescaleDB in Docker (`nms-db`, 127.0.0.1:5432) — restart
  policy set, comes back on boot
- snmpsim in Docker (`nms-snmpsim`, 127.0.0.1:1161/udp)
- Python 3.14 venv at `~/nms/.venv`
- FastAPI + uvicorn as **`nms-api.service`** — no longer started by hand

**Credentials (dev only)**
- `DB_URL=postgresql://nms:nms_dev_only@127.0.0.1:5432/nms`
- `TENANT_ID=1e7e1955-3f93-4575-b158-615f00a26e5c` (tenant `lab`)
- Site `home`, collector key `optiplex-01`. `INGEST_TOKEN` is in `.env`.
  Only its SHA-256 hash is stored — a lost token cannot be recovered, only
  reissued with `scripts/create_collector_key.py`. **Do not wrap the value in
  angle brackets** when pasting into `.env`; `load_env()` reads everything
  after `=` literally and you get a 401 that looks like a code bug.

---

## Where the work happens — three surfaces, one machine

The OptiPlex at `192.168.4.181` is the only place the stack runs. There are
three ways to reach it, and knowing which surface you are on is the difference
between doing the work and reporting that you cannot.

| Surface | Reaches the OptiPlex | Use for |
|---|---|---|
| **Claude Code running on the OptiPlex** (terminal, cwd `~/nms`) | yes — it *is* the machine | anything: docker, psql, systemctl, snmpwalk, the scorer, git |
| **VS Code Remote-SSH from the MateBook** | yes | hand editing, reading journals, ad-hoc shell |
| **Cowork / claude.ai session** | **no** | research, document authoring, code authoring against a clone, producing paste-ready commands and edits |

`192.168.4.181` is RFC1918. A Cowork session runs in an Anthropic cloud
container with no route to the lab — verified, not assumed — and no amount of
retrying changes it. The same applies to `127.0.0.1:8000`, `127.0.0.1:5432`
and the MikroTik. A Cowork session *can* reach files on the MateBook, but only
through the desktop bridge and only for folders explicitly connected in the
Claude desktop app.

**This is a division of labour, not a blocker.** When a Cowork session needs
something done on the machine, the correct move is to produce an exact,
paste-ready command block or edit set and hand it to the Claude Code session
on the OptiPlex. When it needs to *see* a result, ask for the output to be
pasted back. Stopping at "I cannot reach the OptiPlex" is the wrong answer and
has been given more than once.

Rules that follow:

- Anything touching the live stack — docker, psql, systemctl, snmpwalk, the
  scorer, `git push` — runs on the OptiPlex. A Cowork session never asserts a
  result it did not see.
- When a Cowork session describes the state of the machine, it is quoting
  something pasted to it, and should say so. This file's opening claim —
  *state below was verified on the machine, not assumed* — only holds while
  that distinction is kept.
- **Never paste `.env` contents or `INGEST_TOKEN` into a Cowork session.** It
  cannot use them, and they persist in a transcript. A leaked token can only
  be reissued, never recovered.
- `C:\ARK\NMS` on the MateBook is downloaded chat artifacts — reference only,
  never canonical. `~/nms` on the OptiPlex is the working tree, and GitHub is
  the transport between the two. Shipping code through chat zips previously
  cost a hash-by-hand reconciliation and one temporarily lost API.

---

## Host configuration outside the repo

**Git captures these only via `deploy/`. A rebuilt OptiPlex loses anything
that is not there.**

`/etc/snmp/snmpd.conf` — `master agentx` already present; added:

```
agentXPerms 0660 0550 Debian-snmp _lldpd
```

`/etc/default/lldpd` — added:

```
DAEMON_ARGS="-x"
```

Then `systemctl restart snmpd && systemctl restart lldpd` (master first).
Verify with `snmpwalk -v2c -c public 127.0.0.1 1.0.8802.1.1.2`.

Without this, `lldpd` sees neighbours but net-snmp does not serve the
LLDP-MIB, so the collector reads zero neighbours from the OptiPlex.

*Gotcha:* a freshly restarted `lldpd` has an empty neighbour table for up to
30 seconds. An immediate walk of `1.0.8802.1.1.2.1.4` returns "No Such Object"
and looks like a failed config. Wait, then re-check.

**Now scripted: `deploy/host-setup.sh`.** Run `sudo ./deploy/host-setup.sh
--check` to report drift while changing nothing, or without `--check` to
apply. It is idempotent and restarts snmpd/lldpd **only if it changed a
file**, so a run against an already-configured host is a true no-op and is
safe while polling is live. It refuses rather than guesses when an existing
directive conflicts with what it expects.

The manual steps above are kept deliberately. The script records *what* to
do; the paragraphs above record *why*, and the why is what makes it
reviewable when it fails on a host that is not this one.

---

## Repository — `github.com/jsequera-py/sovereign-nms` (private)

Working tree `~/nms` on the OptiPlex. **Push after every real change.**

```
collector/          snmp.py mib.py lldp.py fdb.py store.py topology.py poll.py
server/             app.py (FastAPI)  ingest.py (run processing)
common/             wire.py (collector <-> server payload contract)
migrations/         001..006  (006 = the interface_rate view)
scripts/            migrate.sh  reset_data.sh  score_topology.py
                    create_collector_key.py  poll_cycle.sh
sim/                topology.yaml (GROUND TRUTH)  genfleet.py  data/*.snmprec
                    walks/optiplex_real.snmpwalk
deploy/             nms-api.service  nms-collector.service
                    nms-collector.timer  host-setup.sh
                    install-units.sh
design/             six .dc.html artboards + canvas.json
                    (visual language — interface-design.md §8)
docker-compose.yml  ROADMAP.md  HANDOFF.md  identity-design.md
                    interface-design.md  working-method.md
                    inventory.yaml  inventory.generated.yaml
                    inventory.replay.yaml
```

`.gitignore` covers `.env`, `.venv/`, `__pycache__/`, `sim/data/*.snmprec`,
`inventory.generated.yaml`.

**`deploy/` is authoritative; `/etc/systemd/system` is a copy.** Editing a
unit in the repo does not change the running system, and systemd will not
tell you. Two scripts make that checkable rather than merely asserted:

| Script | Covers | Check without changing anything |
|---|---|---|
| `install-units.sh` | `.service` and `.timer` files against their installed copies | `sudo ./deploy/install-units.sh --check` |
| `host-setup.sh` | snmpd `master agentx` and `agentXPerms`, lldpd `-x` | `sudo ./deploy/host-setup.sh --check` |

Both are idempotent, both copy and reload nothing when already in sync, and
both are therefore safe to run while polling is live. Neither restarts a
long-running service for you: `install-units.sh` prints the restart command
and stops, because restarting `nms-api.service` mid-cycle drops a collector
POST — and a dropped POST is indistinguishable from a dead collector in
`minutes_since_last_run`. A monitoring system must not manufacture its own
false alarms.

Run both `--check` modes after any pull, and before concluding that a
configuration problem is a code problem.

---

## Running it

```bash
cd ~/nms
docker compose up -d                      # db + snmpsim (both auto-restart)
.venv/bin/python sim/genfleet.py          # regenerate fleet from ground truth
./scripts/reset_data.sh                   # wipe collected data (keeps schema)

# nms-api.service is already running; no manual uvicorn needed
.venv/bin/python -m collector.poll --inventory inventory.generated.yaml
.venv/bin/python -m collector.poll --inventory inventory.yaml   # real devices

# Direct mode (local debugging only — same processor, no HTTP)
.venv/bin/python -m collector.poll --direct --inventory inventory.generated.yaml

.venv/bin/python scripts/score_topology.py
```

The timer polls both inventories every 5 minutes, so manual polling is only
needed after a `reset_data.sh` or while testing a change. When you do poll by
hand, poll **both** inventories — polling only one leaves the other's evidence
to age past the 30-minute freshness window, and the next scorer run looks like
a regression when it is only staleness.

Always use `psql ... -P pager=off` — the pager otherwise locks the terminal
at `(END)`.

---

## Reachability (migration 005)

`process_run()` used to do `if not obs.reachable: unreachable += 1; continue`,
counting unreachable devices and discarding `reach_status`. It now records
every observation.

Two tables, both cleared by `reset_data.sh`:

- **`device_reachability`** — current state, one row per polled target.
  Key `(tenant_id, poll_target)`, `device_id` nullable (`ON DELETE SET NULL`),
  `reach_status`/`error`/`last_attempt_at`/`changed_at`. Updated every run.
- **`device_reachability_change`** — append-only, one row **only when
  `reach_status` differs** from what was stored. Not one row per run: at 288
  cycles/day across 500 devices that would be ~144k rows/day of
  near-identical data. Alerting needs state changes, not samples, and
  flapping is only visible in transitions.

**Why keyed on `poll_target`, not `device_id`:** an unreachable observation
carries only the inventory name. Identity resolution needs a successful poll,
so a target never reached has no device row at all, and one that has been
reached is keyed on sysName, not the inventory name that got it polled.

**Consequence for Phase 3.1:** `optiplex` and `optiplex-replay` are two rows
pointing at one `device_id`. Correct — in production a device with two
management paths is the real version of this — but alerting must aggregate to
device level, or one device unreachable on one path raises two alerts.

Nothing consumes this data yet. That is Phase 3.1, and the history will exist
when it arrives.

---

## Removed behaviour — do not re-document it

Older notes said *"two polls are required on a fresh database; the first
creates placeholders, the second upgrades them to interface fidelity — that
self-correction is the demo."* **No longer true.**

`poll_one()` built links per device inside the device loop. `process_run()`
does all devices first, then all links. Measured: the first poll alone
produces all 16 links at interface fidelity, zero device fidelity, in both
modes.

Device-fidelity links are not suppression-capable, so the system is correct
one poll sooner. ROADMAP 5.4 is re-based on **topology change** — move a
cable, poll again, watch the graph correct itself.

Placeholders still exist for neighbours outside the inventory (the eeros, the
MateBook dock). They stay device-fidelity because they are never polled, and a
second poll does not change that.

---

## Architectural decisions (settled — do not relitigate without cause)

**Schema**
- Interfaces key on `(device_id, if_name)`, **never `ifIndex`**
- Links are **undirected physical facts**; dependency direction is a separate
  derived layer with its own confidence
- Links carry **fidelity**: `interface` (suppression-capable) or `device`
- Canonical ordering prevents one cable being stored twice
- **Confidence is computed from evidence, never written directly**
- **`link.pinned` exempts a link from the rollup entirely.**
  `rollup_confidence()` filters `WHERE pinned = FALSE`, and `auto_edge_pct`
  counts `NOT pinned` as the automatic share. A pinned link never decays and
  no absence of evidence can retract it — a permanent dependency claim, and
  therefore a permanent suppression path. Decide in Phase 3 whether
  suppression may act on one. Confirmed in source 2026-08-25
  (`migrations/001_core_schema.sql:162`, `collector/topology.py:256`,
  `server/ingest.py:182`); previously undocumented.

**Identity**
- **Resolving:** serial, chassis_id, base_mac, sysName
- **Corroborating only:** mgmt_ip — recorded, never establishes identity
- Locally-administered MACs (docker0, br-*, veth*) excluded
- Generic sysNames refused. `GENERIC_SYSNAMES` lives in
  **`collector/store.py`** (the identity module), imported by `lldp.py` and
  `topology.py`. One definition — there were three at one point, and they
  disagreed.
- `resolve_device()` never auto-merges on multi-match: it logs and picks the
  strongest claim. See the identity design section for the case it misses.

**Wire contract**
- A collector reports **what it saw, never what it concluded**
- The server derives `tenant_id` and `site_id` from the collector credential.
  A payload may never state its own tenant. This one property is what keeps a
  future hosted model safe.
- `--direct` runs the *same* `process_run()`, not a parallel implementation.
  `server.ingest` is imported **lazily** inside the `--direct` branch so a
  thin collector shipped without `server/` still works (Phase 6.2).
- `reach_status` is `"ok" | "unreachable" | "filtered"`. `SnmpEmpty` on the
  system walk means filtered (device answered, view restricted); `SnmpError`
  means unreachable. Both keep `reachable=False` — `reachable` is the "can I
  use this" flag, `reach_status` is the diagnosis.

**Evidence**
- One row per `(link, source, reporter_device)`. Re-polling refreshes.
- Freshness window **30 minutes**; must exceed the poll interval
- **Confidence is a formula, not a table.** `base = max(SOURCE_BASE of the
  sources present)`; `score = base + 0.15 × (independent confirmations − 1)`;
  capped at `AUTO_CEILING` 0.95, or 1.00 when a `manual` source is present,
  because nothing automated reaches certainty. Bases in
  `collector/topology.py`: manual 1.00 · lldp 0.65 · cdp 0.60 · api 0.60 ·
  mac_table 0.40 · arp 0.30 · route 0.25 · traceroute 0.25 · inferred 0.20.
  One-sided LLDP is therefore 0.65 and two-sided 0.80 — the two numbers this
  file used to quote were one case of the rule, not the rule itself. Three
  independent confirmations hit the 0.95 ceiling, which the lab fleet does
  produce.

**FDB inference is strict**
- A port qualifies only with exactly one known device and ≤ 6 total MACs
- Finds leaf attachments; deliberately does **not** find switch-to-switch links
- Loosening this is how false links appear, and a false link becomes a false
  dependency, which suppresses a real outage

**The LLM decides nothing.** Discovery, direction and suppression stay
deterministic and auditable. 8B plus grounding; the 70B requirement is cut.

**Adoption**
- **Adoption is governed by scope, never by sighting.** A device outside an
  approved scope is never probed, whatever the topology says about it.
  Discovery draws it; only policy adopts it.
- Link degree is the primary classifier; LLDP capabilities corroborate.
  Same precedence as `mgmt_ip`: recorded, corroborating, never deciding.

---

## Vendor realities discovered on actual hardware

**RouterOS 6.49 "Reset Counters" does not reset the SNMP counters.** Tested
2026-08-25 on `rb951g-lab`: Winbox's Reset Counters on `ether1-gateway`,
followed by a poll 127 s later, showed `delta_octets = +460,823` — the IF-MIB
`ifHCInOctets`/`ifHCOutOctets` had kept climbing throughout. Winbox resets what
Winbox displays, not the MIB. Consequence: the counter-reset-without-reboot
branch of `interface_rate` cannot be exercised on this hardware that way, and a
full reboot is the only means of resetting SNMP counters here — which also
resets `sysUpTime`, so it tests the other branch instead.

**MikroTik RouterOS 6.49 returns `lldpRemLocalPortNum = 0`** for every
neighbour — who is adjacent, but not on which port. It omits
`lldpLocChassisId` entirely and uses PortId subtype 3 (macAddress), putting
the interface name only in PortDesc. This is why `link` supports device-level
fidelity at all. The simulated fleet includes a `routeros_partial` profile
reproducing it.

**Both eero units advertise `SysName: eero`.** The gateway
(`30:34:22:d7:1b:00`) and the 2 AP (`0c:93:a5:24:86:e0`) are distinct hardware
announcing an identical name.

**eero sysDescr carries a serial:** `eero PoE 6 GGC21D0A30272301`. `serial`
outranks chassis MAC in `IDENTITY_PRECEDENCE` and survives a NIC swap.
Currently discarded.

Confirmed 2026-08-20 that this arrives as `lldpRemSysDesc` in the OptiPlex's
own LLDP remote table, not only in the eero's sysDescr. The eero is
cloud-managed and not SNMP-pollable, so a sysDescr-only route would have been
unusable — but the neighbour table gives it to us from a device we already
poll. Cheaper than previously recorded: it needs vendor-specific parsing of a
string we are already receiving, not access to the eero.

**A correctly-configured firewall makes a device look dead.** SNMP to the
MikroTik timed out identically to a powered-off switch. The collector records
the difference now and it is persisted, but nothing acts on it yet.

**LLDP capability bits are wrong in both directions on real hardware.**
Measured 2026-09-13. The MateBook dock reports `lldpRemSysCapSupported`
and `lldpRemSysCapEnabled` both `00`, advertising no capabilities at all.
The Raspberry Pi reports `Wlan, on` because `wlan0` exists on the board,
though it is administratively DOWN and carries no traffic. A rule of
"Bridge or Router or WLAN means infrastructure" would adopt a RetroPie
game console. Two real samples, wrong in opposite directions. This is why
adoption classifies on link degree first.

**RouterOS 6.49 floods LLDP across bridge ports.** Measured 2026-09-13. A
Raspberry Pi on bridge port 4 running `lldpd` reported exactly one
neighbour: `MATEBOOK-D14`, chassis subtype local, PortID
`mac 64:c9:01:a9:42:7e`. The dock sits on bridge port 1, confirmed from
`dot1qTpFdbPort`. The frame crossed the bridge. An 802.1D-compliant bridge
consumes `01:80:c2:00:00:0e` rather than forwarding it.

Consequence: **on this hardware LLDP means same broadcast domain, not
physical adjacency.** That is the assumption the entire evidence model
rests on, and `lldp` carries the highest automatic base at 0.65, so a
phantom link from this path arrives at high confidence and no amount of
FDB strictness catches it. It does not bite today only because the Pi is
not in `inventory.yaml` and nobody asks it what it sees. The first polled
Linux host on that bridge will report a neighbour it is not connected to.

Also measured: the MikroTik transmits no LLDP on its bridge ports. 4
frames received by the Pi in 96 seconds with one neighbour inserted is one
sender at the standard 30-second interval. It receives and reports, which
is all the collector needs, but it is invisible to anything downstream.

---

## Bugs that produced no error while writing wrong data

1. All 13 simulated devices merged into one, via shared `mgmt_ip` — reported
   `polled 13/13 successfully`
2. `base_mac` selected a Docker-generated MAC that changes on restart
3. One cable stored as two link rows at different fidelities
4. Generator FDB not transitive — inference looked trivially easy
5. Generator bridge ports numbered identically to ifIndex
6. Generator learning all of a neighbour's MACs, not just the facing port
7. **Generic sysName used as a resolving identity.** `GENERIC_SYSNAMES` lived
   in `lldp.py`, applied only to LLDP names, never where identity is decided.
   Confirmed live: two eeros sharing one name.
8. **The API path skipped the sysName filter the direct path applied.**
   `_Nbr` in `server/ingest.py` assigned the raw value. Two implementations,
   one fixed, one not.
9. **`reset_data.sh` did not clear the new reachability tables**, so after a
   reset every target still had a stored `ok`, `changed=False`, and the first
   poll wrote zero transition rows. The verification that had just passed
   would not have passed twice.

**Every one reported success.** This is why the scorer exists before the GUI.

**A near-miss worth remembering:** a verification run once skipped
`reset_data.sh` as "destructive" and scored the existing database. It returned
88.9% / 100% and looked like a pass — but no poll had run, so the change under
test was never exercised. **Always reset and re-poll when a change affects
device creation.**

**Also:** a raw link-table diff is *not* a valid regression test.
`link.device_a`/`device_b` are canonically ordered by UUID and
`reset_data.sh` regenerates UUIDs, so column assignment varies between
rebuilds. Normalise the pair before diffing.

---

## Ground truth — `sim/topology.yaml`

14 devices, 18 links. **This file is the answer key.** Editing it to make
discovery look better deletes the only honest measurement in the project.

| LLDP mode | Count | Expectation |
|---|---|---|
| both | 12 | found trivially |
| one | 1 | found, lower confidence (0.65) |
| none | 4 | needs FDB inference |
| hidden switch | 1 | not findable |

Currently missed: `acc-sw-02 ↔ acc-sw-03` (unmanaged switch between) and
`acc-sw-03 ↔ dist-sw-01` (switch-to-switch, no LLDP). Both would require
cross-device FDB correlation, which risks precision.

---

## The scheduler — Phase 1.2, installed 2026-08-20

`nms-collector.timer` → `nms-collector.service` → `scripts/poll_cycle.sh`.
All three live in `deploy/` and `scripts/`; the installed copies under
`/etc/systemd/system` were verified byte-identical to the repo copies.

**5-minute interval, ±30s jitter.** The interval is one decision with
`link_evidence_fresh`, not two: at 5 minutes you survive five consecutive
failed polls before evidence ages out; at 15 minutes you survive one. If the
interval ever grows, the freshness window grows with it.

**One `ExecStart` calling a wrapper, not two `ExecStart` lines.** Two
`ExecStart=` lines in a `Type=oneshot` unit abort on the first failure, which
couples two independent failure domains. `inventory.generated.yaml` is
gitignored, so its absence on a fresh clone or a rebuilt OptiPlex would stop
the MikroTik and the OptiPlex from being polled at all — a simulation
artifact taking the real hardware offline. `poll_cycle.sh` runs both
inventories regardless and still exits non-zero if either failed. Sequential
on purpose: `optiplex` and `optiplex-replay` resolve to the same device row,
and concurrent identity resolution has never been tested.

**`AccuracySec=1s`.** systemd defaults to a minute of scheduling slop for
power saving. Left at the default the effective interval becomes 5:00–6:30,
and the 24h cycle count falls short for reasons unrelated to the collector.

**`Persistent=true` is deliberately absent.** `systemd.timer(5)`: it only
affects timers configured with `OnCalendar=`. On a monotonic timer it is
inert. `OnBootSec=2min` is what actually covers the reboot case. The
rationale for `Persistent=` in `phase-1.2-plan.md` is wrong — do not copy it
into a Phase 6.2 site-collector unit.

**`Wants=nms-api.service` alongside `After=`.** `After=` only orders units
within one start transaction, and a timer fires its service in its own
transaction, so `After=` alone was a no-op. Even with `Wants=`, "active" is
not "listening" — the first post-boot cycle can still lose the race and fail.
That is what the 5-minute retry is for.

**Measured 2026-08-20:** a full cycle is ~6.3s wall clock (14 simulated
devices plus 3 real) against `TimeoutStartSec=240` — roughly 40× headroom.
Overlap is not a practical risk at this fleet size. Observed spacing between
consecutive timer-driven cycles: 5m 21s.

**Both closed 2026-08-26.**

*Overlap (step 3), tested on the real unit.* `nms-collector.service` was
started manually and a second `systemctl start` issued 1 s later while the
cycle was still `activating`. `MainPID` read 2257805 before and after, exactly
one `poll_cycle.sh` process existed at both readings, and `systemctl
list-jobs` showed a single merged job. The journal records one
`Starting`/`Finished` pair, `Result=success`, 7.2 s wall clock. systemd merges
the second start rather than running a concurrent poll — confirmed, not
assumed.

*Wedged run (step 4), tested on a replica.* A transient oneshot unit with
`TimeoutStartSec=20` running `sleep 400` was terminated at 20 s with
`Result=timeout` — killed, not queued, and nowhere near its 400 s runtime.
The directive behaves the same on `nms-collector.service`
(`TimeoutStartSec=240`). What the replica does **not** cover is whether
killing `poll_cycle.sh` leaves orphaned python children; systemd's default
`KillMode=control-group` should take them, but that specific claim is
untested. Cheap to settle when the sim rig is next perturbed — a drop-in
`TimeoutStartSec=15` plus one unroutable target with a long SNMP timeout.

*How this was measured, including the wrong way.* The first overlap attempt
was invalid and is kept because the failure is instructive: `systemd-run`
blocks on the start job by default, so the first run had already timed out
before the "concurrent" start was issued. `MainPID before: 0` was the tell.
`--no-block` is required to hold a oneshot in `activating` while a second
start is attempted.

---

## Working rules

- **Run the scorer after every collector or generator change**, with a reset
  and a re-poll. Nine real bugs have reported success while corrupting data.
- **Precision over recall.** A missing link is a gap; a false link suppresses
  a real outage.
- **Push after every real change.**
- Ports bind to `127.0.0.1`. Reach the database from the laptop over an SSH
  tunnel, not by exposing it.
- Work one step at a time, verify each, commit each. Do not batch changes
  whose effects cannot be separated.
- Prefer direct, clear language. Challenge assumptions rather than agreeing;
  say when something is wrong and why.

---

## Open questions

1. **Does the topology decay at all without a poll?** See the finding above —
   **the write-path half was answered 2026-08-22**: with the timer running the
   rollup does set `state = stale`, and zeroes `confidence` doing it.
   Probably the highest-value question here, because it changes what a UI can
   honestly display.
   **Partially settled 2026-08-21.** `interface-design.md` §4 computes
   freshness on the *read* path (option 2 of the three above) for display
   purposes, and defines the render rule: a stale link keeps its last
   confidence and loses its authority — `0.80 · unverified 13h`, never a
   decayed number, because inventing a lower value fabricates a measurement.
   That separates the display question from the write-side one. **Still open:
   should `rollup_confidence()` also set `link.state` on write?** The two may
   coexist; they must not disagree on screen.
   **Write path confirmed in source 2026-08-25.** `collector/topology.py:270`
   runs `UPDATE link SET confidence = 0, state = 'stale'` when no fresh
   evidence remains — the rollup sets *both*. The remaining decision is one
   line wide: drop `confidence = 0` from that statement. `state` already
   carries "do not trust this", and leaving the column at its last computed
   value makes `0.80 · unverified 30h` readable straight from the row,
   retiring the `link_evidence` reconstruction `interface-design.md` §8
   currently mandates. Zero is not a measurement; it is the absence of one
   written as though it were.
   **Settled and implemented 2026-08-26** (`5c7b3fa`). `confidence = 0` is
   gone from the rollup; the statement writes `state = 'stale'` only.
   Verified by ageing one link's evidence to two hours and polling the real
   inventory alone: the link moved `active → stale` with `confidence` intact
   at **0.95**, where the old code would have written `0.00`. Regression run
   first — reset, both inventories re-polled, scorer unchanged at 88.9% /
   100%, 16/16 port pairs, zero false links. The read path can now take the
   last-known number straight from `link.confidence`, so the `link_evidence`
   reconstruction required by `interface-design.md` §8 is no longer needed.
   **What stays open is the display half only:** §4's read-path freshness
   computation and the write-path `state` must not disagree on screen.
2. Cross-device FDB correlation for the last 2 links, accepting precision
   risk?
3. Alert thresholds — static, or baselined per interface?
4. FortiGate/FortiSwitch at a friend's lab: FortiLink or standalone mode, and
   does `lldpRemLocalPortNum` come back non-zero on FortiOS? Authorization
   confirmed; reachability is the open problem — this is the scenario-2 test
   case.
5. **Retention — measured 2026-08-24, before the reset that wiped the sample.**
   `metric_sample` held 395,524 rows / 99 MB over 3d 19h. The clean timer-era
   rate, taken between two timer-era snapshots (238,348 rows at 08-22 19:27 →
   395,524 at 08-24 02:25), is **~121,800 rows/day** — the ~115k estimate was
   good. Structure: 217 interfaces × 2 metrics (`if_in_octets`,
   `if_out_octets`, exactly half each) × ~274 cycles, plus the OptiPlex being
   polled twice a cycle. **~250 bytes/row including indexes**, so ~30 MB/day at
   lab scale and **~561 rows / ~0.14 MB per interface per day**.
   Extrapolated: 500 devices averaging 24 ports is 12,000 interfaces →
   **~6.9M rows/day, ~1.7 GB/day, ~630 GB/year uncompressed.** That is about
   six years on a 4TB NVMe, not one — the earlier one-year claim was wrong by
   roughly 6×. **No TimescaleDB compression policy is
   configured** — that, not row expiry, is the first lever, and it is now a
   Phase 6.4 item with a number attached rather than a question mark.
   Still open: how long evidence lives, and whether rollup precedes expiry.
6. Is the air-gapped variant of scenario 1 a market worth targeting?
7. `vendor` is still inferred client-side, though `sys_object_id` and
   `sys_descr` both travel on the wire. Strictly a conclusion, not an
   observation. Moving `infer_vendor()` server-side would let detection
   improvements reach already-deployed collectors — decide before Phase 6.2.
8. **RESOLVED 2026-08-24 — and the original diagnosis was wrong.** Kept because
   the wrong version is instructive. It said the recorded walk was stale
   (6 interfaces vs 7 live) and should be re-recorded. Measured on the host,
   the difference is entirely Docker plumbing: the recording holds
   `vethd544e8c`, which no longer exists; live holds `veth171ed6e` and
   `veth37eff0c`, which did not exist when it was taken. `br-6c815583b5f7` was
   ifIndex 7 recorded and 5 live — the decision to key interfaces on
   `(device_id, if_name)` validated by data.
   The generator was never the file. `upsert_interfaces()` marks any active
   interface absent from a poll as stale, and `optiplex` and `optiplex-replay`
   resolve to **one** device row while carrying different interface sets, so
   each poll retired the other's veths, twice a cycle, indefinitely.
   Re-recording would have held until the next container restart.
   **Fixed in `c742b30`:** `store.is_ignored_ifname()` drops container-managed
   names (`veth<hex>`, `br-<hex>`, `docker<n>`) inside `_iface_view()`,
   server-side so it reaches already-deployed collectors. Strict on the hex
   suffix — `br-lan` on OpenWrt is a real bridge. Verified after a reset and
   re-poll: scorer unchanged at 88.9% / 100%, OptiPlex 8 interfaces → 3, zero
   Docker-named interface rows, and 226 active / 0 stale across two consecutive
   polls of the same device. The recorded walk never needs re-recording.
9. All simulated devices share `mgmt_ip = 127.0.0.1`, so every poll logs 14
   identity-reassignment lines. Harmless — `mgmt_ip` cannot resolve identity —
   but it buries the case where a management IP genuinely moves.
   **~4,032 such lines a day** with the timer running — journal noise at a
   volume that will hide something real.
10. **`discovery_run.started_at` equals `finished_at`.** Observed 2026-08-20,
    every row in the table, identical to the microsecond — one timestamp
    written into both columns at completion. The journal shows the simulated
    poll spending ~5s on SNMP before the POST, so a real `started_at` would
    sit that much earlier. Consequence: **run duration is unmeasurable from
    the database**, so a collector slowing toward its 5-minute interval — the
    failure you want to catch before it starts missing cycles — is visible
    only in the journal, which the API cannot query. Note the "started but
    never finished" signal is *deliberately* absent: `phase-1.2-plan.md`
    relies on a failed POST writing no row at all, so
    `minutes_since_last_run` grows. **Do not fix this by having the collector
    write a run row locally on failure** — that destroys the liveness signal.
    Either populate `started_at` server-side at request receipt, or drop the
    column as a false measurement. Decide in Phase 3, when collector health
    becomes monitored. Does not affect the 24h exit test, which counts rows.
    Now a stated Phase 3.3 decision in `ROADMAP.md`: run duration is the only
    signal that a collector is slowing toward its interval, and Phase 5
    screen 6 (collector health) cannot show it otherwise.
    **Cause found in source 2026-08-25 — both proposed remedies were
    unnecessary.** `store.connect()` opens with `autocommit=False`, and
    `process_run()` INSERTs the run row at `server/ingest.py:103` while
    UPDATEing `finished_at = now()` at line 190, both inside one
    transaction. PostgreSQL's `now()` is `transaction_timestamp()`, so every
    call within a transaction returns the identical value. The fix is
    `clock_timestamp()` on the UPDATE. **It still does not answer the
    question you want answered:** those timestamps bracket ingest
    processing, not the poll. The ~5s SNMP phase happens on the collector
    before the POST, so measuring a collector slowing toward its interval
    needs the collector to report its own start time on the wire — an
    observation, which the wire contract permits.
    **Fixed and verified 2026-08-26** (`a3ec23c`). `nms-api` was restarted in
    the gap between cycles at 03:45:40 UTC; the 03:50 cycle then wrote two
    rows with `duration` 0.562 s and 0.058 s, against `00:00:00` on every
    earlier row. **What remains open is the residual, not the bug:** those
    durations measure ingest processing only. The collector's ~5 s SNMP phase
    is still unmeasured, so a collector slowing toward its interval stays
    invisible until it misses a cycle. Closing that needs a `poll_started_at`
    field on the wire — an observation, which the contract permits. Phase 3.3.
11. **Largely resolved 2026-08-24 by the same fix as question 8 — and its
    proposed remedy was aimed at the wrong subsystem.** It blamed lldpd and
    proposed `configure system interface pattern`. The churning table is
    IF-MIB `ifDescr` served by net-snmp, not lldpd's local port table, so that
    change would have fixed nothing measurable. `store.is_ignored_ifname()`
    now drops those names before any interface row is written (`c742b30`).
    **What remains is narrow:** lldpd still advertises veths as local LLDP
    ports. No neighbour is ever seen on them so no false link results, but if
    one ever were, the link would reference a local interface with no row and
    fidelity would quietly degrade to device level rather than erroring. Worth
    an lldpd filter eventually. Not a Phase 3 blocker.
12. **The scorer keys by device pair, so it cannot see a second link between
    the same two devices.** `discovered_links()` does
    `out[sorted(device_a, device_b)] = row`, while `link_pair_unique` is
    `(device_a, device_b, endpoint_a, endpoint_b) NULLS NOT DISTINCT`. Three
    cases follow and the scorer treats them alike: two device-fidelity rows
    on one pair are impossible, both endpoints being NULL; one device-fidelity
    plus one interface-fidelity row is **bug #3 recurring**, and is silently
    overwritten; two interface-fidelity rows on different ports are a **LAG or
    dual-homed pair — legitimate — and one is dropped from the score**. No
    such pair exists in `sim/topology.yaml`, so the 88.9% baseline is honest
    today; the blind spot opens the moment one does.
    **Closes the `dedup-fix` question, open since 2026-08-19.** Both laptop
    artifacts were hashed 2026-08-26: `scorer/score_topology.py` is
    byte-identical to the deployed file (`4b46a9dd…`), and
    `dedup-fix/score_topology.py` (`e1f98359…`) differs by exactly one
    feature, a duplicate-pair check, across 7 hunks with identical SQL and
    imports. **It was not deployed and should not be:** it flags a LAG as a
    duplicate and collapses it. The correct fix keys on
    `(device pair, endpoint pair)` and reports a defect only when one pair
    carries both fidelities. Do it when a LAG enters the ground-truth fleet.
    `C:\ARK\NMS` needs no further reconciliation.

13. **Unpolled placeholders never refresh `last_seen`.** Observed 2026-09-01:
    both eeros sit frozen at `2026-08-26 04:18:24` and the MateBook dock at
    `2026-08-28 02:15:58`, while every polled device reads within minutes of
    the current time. Yet their links are `active` with fresh evidence, so the
    devices *are* being re-observed every cycle — the link row is updated and
    the device row is not. **Device freshness and link freshness disagree by
    days on the same entity.** Harmless today. It becomes wrong the moment
    Phase 3 alerts on device staleness or Phase 5 renders "last seen" for a
    leaf device — and leaf devices are exactly what a customer's access layer
    is made of. Decide whether an LLDP sighting should touch the placeholder's
    `last_seen`, or whether the UI must read link evidence age instead. Note
    this is the write-path twin of question 1's display half.

14. **`gate.py`'s four input-failure branches are unexercised.** Non-zero
    subprocess exit, empty stdout, invalid JSON, and missing key are each
    written and were read line by line, but none has been made to fire.
    Proving them means temporarily breaking `score_topology.py`, which is a
    change to a file under test, so it was deliberately deferred rather than
    folded into the same step. Each branch is three lines and exits before the
    condition loop, so a broken scorer cannot produce a partial pass — but
    that is an argument from reading, not a measurement. Cheap to settle:
    point `SCORER_CMD` at a stub that exits 1, then at one printing nothing,
    then at one printing `{}`.

15. **LLDP flooding invalidates the adjacency assumption on RouterOS
    bridges.** See Vendor realities. Decide before any second Linux host on
    that bridge enters `inventory.yaml`. Candidate mitigations: require
    two-sided LLDP before a link is created at full `lldp` confidence, or
    cross-check every LLDP claim against the FDB port and reject a claim
    whose peer is not on the reporting port. The second is stronger and the
    data to do it is already collected.

16. **`fdb.py` `learned()` returns True when `status is None`** (line 61).
    A switch that does not serve `dot1dTpFdbStatus` gets its self and mgmt
    addresses counted as learned. That is fail-open on the exact column
    protecting precision, against a stated non-negotiable. Cannot fire on
    RouterOS, which serves the column; it is a scenario-2 multi-vendor
    problem. Proposed fix: when status is unavailable, exclude MACs
    matching the reporting device's own `ifPhysAddress` values, which the
    collector already reads. Preserves the table instead of discarding it
    and does not require the vendor to be honest.

17. **`mgmt_ip` is not captured for LLDP-discovered placeholders.** The Pi
    advertises `MgmtIP 192.168.88.253` in its LLDPDU, and `device.mgmt_ip`
    for `retropie` is NULL. Either `lldpRemManAddr` is not served for it or
    the collector does not read it on the placeholder path. Matters
    directly for ROADMAP 6.5: a management address is what makes a
    candidate pollable without a human typing one.

18. **ROADMAP 5.4 is unfilmable on this lab.** The move-a-cable-and-watch-
    it-correct demo needs an interface-fidelity link on real hardware.
    RouterOS 6.49 returns `lldpRemLocalPortNum = 0`, so every MikroTik link
    is device fidelity with both endpoints NULL and no port for the graph
    to correct: moving the MateBook dock from bridge port 1 to port 3
    leaves the link row byte-identical. Only the OptiPlex can name its own
    ports, and its one relevant port goes to an eero that cannot be moved.
    Either 5.4 is demonstrated on the simulated fleet, or the lab needs one
    switch that reports its local port correctly. The FortiSwitch in open
    question 4 is the candidate.

---

## Result — the 24-hour exit test, run 2026-08-22 19:27 UTC

**Passed 5 of 6. Accepted with the exception recorded below.**

| # | Check | Observed | Verdict |
|---|---|---|---|
| 1 | `minutes_since_last_run` | 2.9 | pass |
| 1 | `cross_site_links` | 0 | pass |
| 3 | `discovery_run` over 24h | **546** | pass (band 540–560) |
| 2 | scorer | 88.9% / 100%, 0 false links | pass |
| 4 | link states | 18 active, **1 stale** | **fail** |
| 5 | `metric_sample` | 238,348 rows / 59 MB | recorded |
| 6 | journal | 273 `Finished`, zero `fail` | pass |

**Two independent measurements agree to the row.** 273 journal cycles × 2
inventories = 546, which is the `discovery_run` count exactly. Zero failed
cycles. The window was verified clean before measuring: uptime 60h36m, so no
reboot inside it.

**Check 4 in full.** One stale link: `rb951g-lab ↔ 64:c9:01:a9:42:7e`, the
MateBook dock placeholder (`unpolled`, no vendor, no site). Both evidence rows
— `lldp` and `mac_table`, reported by the MikroTik — last observed
2026-08-21 13:30:15 UTC, when the laptop left the dock. The MikroTik itself was
polled 30s before the test. Nothing simulated decayed: the scorer's 16/16 and
the link table do not disagree.

**The pass condition was mis-specified, and that is the finding.** Written
2026-08-20 while all 19 links were fresh, it assumed every endpoint would still
be present 24 hours later — but 3 of the 19 depend on real hardware, one of
which is a laptop that is not a fixture of the lab. **Amended condition for
future runs, dated 2026-08-22:** no *simulated* link in `stale`; a
real-hardware link may age out and must be attributed to a named device before
the run is accepted. The original condition is not retroactively relaxed — this
run stands recorded as 5/6.

**Retention data point (open question 5).** 238,348 `metric_sample` rows and
59 MB after ~47h of unattended polling on 17 targets. Do not extrapolate yet:
it is unconfirmed whether a `reset_data.sh` ran inside that window, so the
per-day rate is a lower bound until `min(ts)` is checked.

The recipe below is retained for re-runs.

---

## The recipe — the 24-hour exit test

Run after **20:40 UTC on 2026-08-21** (14:40 local, UTC−6). Do not poll
manually first.

```bash
cd ~/nms
date -u
set -a; . ./.env; set +a          # load INGEST_TOKEN

# 1. liveness
curl -s -H "Authorization: Bearer $INGEST_TOKEN" localhost:8000/v1/health

# 2. topology still current, WITHOUT polling first
.venv/bin/python scripts/score_topology.py

# 3. cycles over the last 24h
psql "postgresql://nms:nms_dev_only@127.0.0.1:5432/nms" -P pager=off -c \
  "SELECT count(*) FROM discovery_run WHERE started_at > now() - interval '24 hours';"

# 4. no link decayed
psql "postgresql://nms:nms_dev_only@127.0.0.1:5432/nms" -P pager=off -c \
  "SELECT state, count(*) FROM link GROUP BY state;"

# 5. retention data point — hypertable_size, NOT pg_total_relation_size,
#    which reports near-zero because the data lives in chunks
psql "postgresql://nms:nms_dev_only@127.0.0.1:5432/nms" -P pager=off -c \
  "SELECT count(*) FROM metric_sample;"
psql "postgresql://nms:nms_dev_only@127.0.0.1:5432/nms" -P pager=off -c \
  "SELECT pg_size_pretty(hypertable_size('metric_sample'));"

# 6. failed cycles
systemctl list-timers nms-collector --no-pager
journalctl -u nms-collector --since "24 hours ago" --no-pager | grep -c Finished
journalctl -u nms-collector --since "24 hours ago" --no-pager | grep -i fail
```

| # | Check | Pass condition |
|---|---|---|
| 1 | `minutes_since_last_run` | under 6 |
| 1 | `cross_site_links` | still 0 |
| 2 | scorer | 88.9% recall / 100% precision |
| 3 | `discovery_run` count | **540–560** — not 576, see below |
| 4 | link states | no row in `stale` |
| 5 | `metric_sample` | record rows and size; this is the retention input |
| 6 | journal | ~274 `Finished`, zero `fail` lines |

**On the expected count.** The plan predicted 576 (288 cycles × 2
inventories). `RandomizedDelaySec=30s` adds ~15s on average, so the effective
interval is ~5m 15s → ~274 cycles → **~548 rows**. That shortfall is the
jitter working as specified, not a fault. Investigate below ~530.

**Run check 3 before check 2.** Per the decay finding above, the scorer
reports 88.9% whether or not anything polled, so check 2 alone does not prove
the scheduler ran.

**Then 1.3 counter deltas**, then Phase 2 (direction) and Phase 3
(suppression). Phase 3's exit test — **one incident instead of forty alerts,
with root cause and audit trail** — is the sellable claim.

**Implement the identity veto rule before Phase 2.** Direction inference
leans hard on device rows being right.
