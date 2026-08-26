# Sovereign NMS — Session Handoff

Paste this into a new chat, along with `ROADMAP.md`, to resume.

Last updated 2026-08-25. State below was verified on the machine, not assumed.

---

## START HERE

### Verified state (2026-08-25)

Most recent commits at the time of writing. The docs commit that carries this
file sits on top of them, so HEAD is one ahead of this list by design — do not
"correct" it.

```
7f7730e docs: repo map — design/, working-method.md, inventory.replay.yaml, 006
22b9244 docs: interface-design §5 — rate view constraints for phase 5
0bdadb2 docs: mark 1.3 done, correct the 32-bit wrap claim
d93da42 docs: close 1.3 in handoff, record RouterOS counter-reset finding
e85dd81 migrations: 006 interface_rate view, rates computed on read
ecc4362 ingest: persist sys_uptime_ticks as a device-level metric sample
bd1dc50 inventory: move optiplex-replay out of the scheduled poll
c742b30 ingest: ignore container-managed interfaces (veth, br-<hex>, docker0)
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

**Deliberately not done yet:** the overlap test (make a cycle run long and
confirm systemd refuses a concurrent start) and the wedged-run test (confirm
`TimeoutStartSec` kills rather than queues). Steps 3 and 4 of the order of
work in `phase-1.2-plan.md`. Neither is exercised by the 24h exit test, so
both remain open.

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
