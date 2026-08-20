# Sovereign NMS — Session Handoff

Paste this into a new chat, along with `ROADMAP.md`, to resume.

Last updated 2026-08-21. State below was verified on the machine, not assumed.

---

## START HERE

### Verified state (2026-08-21)

```
a2175bc (HEAD -> master, origin/master) deploy: db restart policy, systemd unit for the ingest API
ed6f9f4 scripts: wipe device_reachability tables on reset
eb56552 ingest: persist per-device reachability before the scheduler lands
284ee86 docs: correct 1.1 exit test — link tables agree, but not byte-for-byte
98b3794 docs: rewrite HANDOFF, mark Phase 1.1/1.4 done, retire the two-poll demo
15b657b collector: add ingest API POST path, make it the default
```

Working tree clean, local and origin in sync. `migrations/` holds 001–005.
`deploy/nms-api.service` is in the repo. `nms-db` and `nms-snmpsim` up,
`nms-api` active.

**Not installed:** `nms-collector.timer` — `systemctl is-enabled` returns
`not-found`, zero timers listed.

**Database currently holds the simulated fleet only** — 14 devices, 16 active
links, 14 reachability rows. The real devices (`optiplex`, `rb951g-lab`, and
the three placeholders) were wiped by a `reset_data.sh` during verification and
never re-polled. Poll `inventory.yaml` to bring them back.

### Two files exist outside the repo — commit them first

Both were produced in chat and never landed in git. This is the same drift
that once cost a whole session to reconcile.

1. **`HANDOFF.md`** — the copy in the repo is the older version from `98b3794`
   ("Last updated 2026-08-20"). This file supersedes it.
2. **`identity-design.md`** — the design note summarised below. Not in the
   repo at all.

Both are on the MateBook at `C:\ARK\`. Copy into `~/nms`, commit, push.

### Then: install the collector timer

Phase 1.2 step 2. Everything it depends on is done and reboot-proven. The
ready-to-paste prompt is at the bottom of this file under **Next action in
full**.

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
| 1.2 Scheduler — step 2, the timer | **next action** |
| 1.3 Counter deltas | not started |
| 1.4 Real device in the pipeline | **done** |
| 2 Dependency direction | not started — settle identity design first |
| 3 Alerting and suppression | not started — this is the sellable demo |
| 4 Syslog and the AI layer | not started |
| 5 Interface | not started |
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

Measured 2026-08-21. Last poll **776 minutes** earlier — 13 hours, 25× the
30-minute `link_evidence_fresh` window. The scorer still reported
**88.9% / 100%** with 16 links `active`.

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

**Neither snmpd.conf nor /etc/default/lldpd is mirrored in `deploy/`.** Only
`nms-api.service` is. Worth fixing.

---

## Repository — `github.com/jsequera-py/sovereign-nms` (private)

Working tree `~/nms` on the OptiPlex. **Push after every real change.**

```
collector/          snmp.py mib.py lldp.py fdb.py store.py topology.py poll.py
server/             app.py (FastAPI)  ingest.py (run processing)
common/             wire.py (collector <-> server payload contract)
migrations/         001..005
scripts/            migrate.sh  reset_data.sh  score_topology.py
                    create_collector_key.py
sim/                topology.yaml (GROUND TRUTH)  genfleet.py  data/*.snmprec
                    walks/optiplex_real.snmpwalk
deploy/             nms-api.service
docker-compose.yml  ROADMAP.md  HANDOFF.md  inventory.yaml
                    inventory.generated.yaml
```

`.gitignore` covers `.env`, `.venv/`, `__pycache__/`, `sim/data/*.snmprec`,
`inventory.generated.yaml`.

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

Re-poll the simulated fleet after polling only real devices, or the next
rollup marks its evidence stale.

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
- Two-sided LLDP → 0.80. One-sided → 0.65.

**FDB inference is strict**
- A port qualifies only with exactly one known device and ≤ 6 total MACs
- Finds leaf attachments; deliberately does **not** find switch-to-switch links
- Loosening this is how false links appear, and a false link becomes a false
  dependency, which suppresses a real outage

**The LLM decides nothing.** Discovery, direction and suppression stay
deterministic and auditable. 8B plus grounding; the 70B requirement is cut.

---

## Vendor realities discovered on actual hardware

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
Currently discarded — extracting it needs vendor-specific sysDescr parsing.

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

## After the timer

**24h exit test** — after a full day untouched, with no manual poll first:

1. `/v1/health` → `minutes_since_last_run` under one interval,
   `cross_site_links` still 0
2. `scripts/score_topology.py` → still **88.9% / 100%** *without polling first*
3. `SELECT count(*) FROM discovery_run WHERE started_at > now() - '24h'`
   → close to 576 (two inventories × 288 cycles), shortfall explained
4. No link in `stale` state
5. `metric_sample` row count and on-disk size recorded, for the retention
   decision

Note point 2 is weaker than it looks, given the decay finding above — the
scorer reads 88.9% whether or not anything polled. Check point 3 first.

**Then 1.3 counter deltas**, then Phase 2 (direction) and Phase 3
(suppression). Phase 3's exit test — **one incident instead of forty alerts,
with root cause and audit trail** — is the sellable claim.

**Implement the identity veto rule before Phase 2.** Direction inference leans
hard on device rows being right.

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

1. **Does the topology decay at all without a poll?** See the finding above.
   Probably the highest-value question here, because it changes what a UI can
   honestly display.
2. Cross-device FDB correlation for the last 2 links, accepting precision
   risk?
3. Alert thresholds — static, or baselined per interface?
4. FortiGate/FortiSwitch at a friend's lab: FortiLink or standalone mode, and
   does `lldpRemLocalPortNum` come back non-zero on FortiOS? Authorization
   confirmed; reachability is the open problem — this is the scenario-2 test
   case.
5. Retention: how long do metrics and evidence live before rollup? One cycle
   writes ~400 `metric_sample` rows on a 14-device fleet; at 5-minute
   intervals that is ~115k rows/day. Becomes real the moment the timer runs.
6. Is the air-gapped variant of scenario 1 a market worth targeting?
7. `vendor` is still inferred client-side, though `sys_object_id` and
   `sys_descr` both travel on the wire. Strictly a conclusion, not an
   observation. Moving `infer_vendor()` server-side would let detection
   improvements reach already-deployed collectors — decide before Phase 6.2.
8. `sim/walks/optiplex_real.snmpwalk` is stale (6 interfaces vs 7 live), so
   each poll marks the other's extra interface stale. In Phase 3 an interface
   flapping state every poll is a false-alert generator.
9. All simulated devices share `mgmt_ip = 127.0.0.1`, so every poll logs 14
   identity-reassignment lines. Harmless — `mgmt_ip` cannot resolve identity —
   but it buries the case where a management IP genuinely moves.

---

## Next action in full

Paste into Claude Code on the OptiPlex, after committing the two loose files.

```
Phase 1.2 step 2 — the collector timer. The stack survives a reboot
(verified), so it is safe to schedule polling.

CREATE — keep both files in the repo under deploy/, then install into
/etc/systemd/system.

deploy/nms-collector.service

  Type=oneshot
  User=jsequera
  WorkingDirectory=/home/jsequera/nms
  EnvironmentFile=/home/jsequera/nms/.env
  After=nms-api.service
  TimeoutStartSec=240
  ExecStart=/home/jsequera/nms/.venv/bin/python -m collector.poll \
              --inventory inventory.generated.yaml
  ExecStart=/home/jsequera/nms/.venv/bin/python -m collector.poll \
              --inventory inventory.yaml

Two ExecStart lines: the simulated fleet keeps the scorer meaningful, the real
inventory keeps the MikroTik and OptiPlex fresh. Sequential, sim first.

Do NOT prefix either with '-'. A failed poll must fail the unit — the next
cycle retries five minutes later, and a masked failure is how a monitoring
system goes blind quietly.

TimeoutStartSec=240 is below the 5-minute interval on purpose: a cycle that
cannot finish inside its interval is a fault to surface, not to absorb.

deploy/nms-collector.timer

  OnBootSec=2min
  OnUnitActiveSec=5min
  RandomizedDelaySec=30s
  Persistent=true
  WantedBy=timers.target

WHY 5 MINUTES — record this as a comment in the timer file

link_evidence_fresh is 30 minutes. A 5-minute interval buys five consecutive
failed polls before evidence ages out; 15 minutes buys one. Interval and
freshness window are one decision, not two.

ONE RISK TO NOTE IN A COMMENT

inventory.generated.yaml is gitignored (produced by sim/genfleet.py). If it is
ever missing, the first ExecStart fails every cycle forever.

INSTALL AND VERIFY

  sudo systemctl daemon-reload
  sudo systemctl enable --now nms-collector.timer
  systemctl list-timers nms-collector --no-pager

Wait ~11 minutes, then confirm two cycles landed unattended:

  journalctl -u nms-collector -n 40 --no-pager
  psql "postgresql://nms:nms_dev_only@127.0.0.1:5432/nms" -P pager=off -c \
    "SELECT started_at, finished_at, devices_seen, auto_edge_pct
       FROM discovery_run ORDER BY started_at DESC LIMIT 6;"

Expected: four discovery_run rows from two cycles (two inventories each),
roughly 5 minutes apart, no manual polling.

Do NOT run the 24-hour test yet and do not do the overlap test. Report the
timer listing and the discovery_run rows, then stop.
```
