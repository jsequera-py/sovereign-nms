# Sovereign NMS — Session Handoff

Paste this into a new chat, along with `ROADMAP.md`, to resume.

Last updated 2026-08-20.

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

## Current state

Phase 1.1 (collector ingest client) and 1.4 (real device in the pipeline) are
**done**. Discovery works end to end over the API. Still missing: the
scheduler, counter deltas, direction inference, suppression, and any UI.

| Metric (simulated fleet) | Value |
|---|---|
| Recall | 88.9% (16/18 links) |
| **Precision** | **100%** |
| Port pairs correct | 16/16 |
| Recall on LLDP-reachable links | 100% |
| `auto_edge_pct` | 100.0 |

**This baseline must not regress.** Run the scorer after every change to the
collector or the fleet generator.

**A single poll is now sufficient.** Since `process_run()` became the
persistence path, one poll produces the complete final link table at interface
fidelity, in both modes. The old "poll twice and watch it upgrade" behaviour
is gone — see *Removed behaviour* below.

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
- **System Identity set to `rb951g-lab`** (was the factory default `MikroTik`,
  which is now refused as an identifier)

**Not usable for monitoring:** eero mesh (cloud-managed, no SNMP — but it does
advertise LLDP, so eeros appear as neighbours), Cisco X1000 DSL modem, Luxul
XGS-1008 (unmanaged — useful only as a physical "hidden switch" test).

**Stack**
- Postgres 16 + TimescaleDB in Docker (`nms-db`, 127.0.0.1:5432)
- snmpsim in Docker (`nms-snmpsim`, 127.0.0.1:1161/udp)
- Python 3.14 venv at `~/nms/.venv` · FastAPI + uvicorn

**Credentials (dev only)**
- `DB_URL=postgresql://nms:nms_dev_only@127.0.0.1:5432/nms`
- `TENANT_ID=1e7e1955-3f93-4575-b158-615f00a26e5c` (tenant `lab`)
- Site `home`, collector key `optiplex-01`. `INGEST_TOKEN` is in `.env`.
  Only its SHA-256 hash is stored, so a lost token cannot be recovered —
  reissue with `scripts/create_collector_key.py`.

---

## Host configuration outside the repo

**Git does not capture these. A rebuilt OptiPlex loses them silently and goes
back to reporting zero LLDP neighbours.**

`/etc/snmp/snmpd.conf` — `master agentx` was already present; added:

```
agentXPerms 0660 0550 Debian-snmp _lldpd
```

`/etc/default/lldpd` — added:

```
DAEMON_ARGS="-x"
```

Then `systemctl restart snmpd && systemctl restart lldpd` (master first).
Verify with `snmpwalk -v2c -c public 127.0.0.1 1.0.8802.1.1.2`.

*Gotcha:* a freshly restarted `lldpd` has an empty neighbour table for up to
30 seconds. An immediate walk of `1.0.8802.1.1.2.1.4` returns "No Such Object"
and looks like a failed config. Wait, then re-check.

---

## Repository — `github.com/jsequera-py/sovereign-nms` (private)

Working tree `~/nms` on the OptiPlex. **Push after every real change** — four
file-transfer failures happened in one session before this repo existed, and
reconciling the resulting drift took a whole session of hashing files by hand.

```
collector/          snmp.py mib.py lldp.py fdb.py store.py topology.py poll.py
server/             app.py (FastAPI)  ingest.py (run processing)
common/             wire.py (collector <-> server payload contract)
migrations/         001..004
scripts/            migrate.sh  reset_data.sh  score_topology.py
                    create_collector_key.py
sim/                topology.yaml (GROUND TRUTH)  genfleet.py  data/*.snmprec
                    walks/optiplex_real.snmpwalk
docker-compose.yml  ROADMAP.md  inventory.yaml  inventory.generated.yaml
```

`.gitignore` covers `.env`, `.venv/`, `__pycache__/`, `sim/data/*.snmprec`,
`inventory.generated.yaml`.

---

## Running it

```bash
cd ~/nms
docker compose up -d                      # db + snmpsim
.venv/bin/python sim/genfleet.py          # regenerate fleet from ground truth
./scripts/reset_data.sh                   # wipe collected data (keeps schema)

# API mode (default)
DB_URL="postgresql://nms:nms_dev_only@127.0.0.1:5432/nms" \
  .venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8000 &
.venv/bin/python -m collector.poll --inventory inventory.generated.yaml

# Direct mode (local debugging only — same processor, no HTTP)
.venv/bin/python -m collector.poll --direct --inventory inventory.generated.yaml

.venv/bin/python scripts/score_topology.py
```

Real devices: `--inventory inventory.yaml`. Re-poll the simulated fleet
afterwards or its evidence goes stale and the scorer drops.

Always use `psql ... -P pager=off` — the pager otherwise locks the terminal
at `(END)`.

---

## Removed behaviour — do not re-document it

The old handoff said *"two polls are required on a fresh database; the first
creates placeholders, the second upgrades them to interface fidelity — that
self-correction is the demo."* **That is no longer true.**

`poll_one()` built links per device inside the device loop. `process_run()`
does all devices first, then all links. Measured after the change: the first
poll alone produces all 16 links at interface fidelity, zero device fidelity,
in both modes. Three links (`br-fw-01↔br-sw-01`, both `br-sw-01↔br-rtr-02`
legs) used to arrive device-level and upgrade on poll two.

Device-fidelity links are not suppression-capable, so the system is correct
one poll sooner. ROADMAP 5.4 needs re-basing on **topology change** — move a
cable, poll again, watch the graph correct itself — which is a better demo
anyway.

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
- Generic sysNames refused. `GENERIC_SYSNAMES` lives in **`collector/store.py`**
  (the identity module), imported by `lldp.py` and `topology.py`. One
  definition — there were three at one point, and they disagreed.

**Wire contract**
- A collector reports **what it saw, never what it concluded**
- The server derives `tenant_id` and `site_id` from the collector credential.
  A payload may never state its own tenant. This one property is what keeps a
  future hosted model safe.
- `--direct` runs the *same* `process_run()`, not a parallel implementation.
  `server.ingest` is imported **lazily** inside the `--direct` branch so a
  thin collector shipped without `server/` still works (Phase 6.2).

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
announcing an identical name. Before `eero` was added to `GENERIC_SYSNAMES`
they would have merged into one device row, silently.

**eero sysDescr carries a serial:** `eero PoE 6 GGC21D0A30272301`. `serial`
outranks chassis MAC in `IDENTITY_PRECEDENCE` and survives a NIC swap.
Currently discarded — extracting it needs vendor-specific sysDescr parsing.

**A correctly-configured firewall makes a device look dead.** SNMP to the
MikroTik timed out identically to a powered-off switch. The collector now
records the difference (`reach_status`), but nothing persists or acts on it
yet — see the gap below.

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
   in `lldp.py` and was applied only to LLDP names, never where identity is
   decided. Confirmed live: two eeros sharing one name.
8. **The API path skipped the sysName filter the direct path applied.**
   `_Nbr` in `server/ingest.py` assigned the raw value. Two implementations,
   one fixed, one not.

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

## Next steps

**Phase 1.2 — scheduler** (immediate). Nothing polls on its own, so topology
decays 30 minutes after anyone stops typing. Observed directly: a run reported
`scored=3 stale=16` after only real devices were polled.

Recommendation: **systemd timer, not an internal loop.** Restart, logging and
jitter come free (`RandomizedDelaySec` is exactly the "50 collectors don't
stampede" requirement), and a one-shot process cannot accumulate state between
cycles. 5-minute interval: with a 30-minute freshness window that buys five
consecutive failed polls before decay. Interval and freshness window are one
decision, not two.

**Do this first, before enabling the timer:** persist per-device reachability
per run. `process_run()` currently does `if not obs.reachable: unreachable +=
1; continue` — it counts unreachable devices and discards `reach_status`
entirely. Once a timer runs 288 cycles a day, "which device was unreachable at
03:00, and was it unreachable or filtered?" is unanswerable. That distinction
is what Phase 3.1 is built on, and 3.1 will arrive with no history to test
against unless the record starts now.

Do not "fix" `/v1/health.minutes_since_last_run` by writing a run row locally
on failure. It is a correct liveness signal *because* a failed POST writes
nothing.

**Then 1.3 counter deltas**, then Phase 2 (dependency direction) and Phase 3
(suppression). Phase 3's exit test — **one incident instead of forty alerts,
with root cause and audit trail** — is the sellable claim.

**Before Phase 2, settle the identity design.** The generic-name denylist only
contains names already encountered: it caught `mikrotik` because one was on
the bench and missed `eero` until one appeared. Every unmet vendor is a silent
device merge. The data carries the fix: **a sysName seen against two different
chassis identifiers is not an identity.** Self-correcting, no list to
maintain, consistent with confidence-from-evidence. Keep the denylist as a
fast path; add the learned rule underneath. Direction inference will lean hard
on identity being right.

---

## Working rules

- **Run the scorer after every collector or generator change**, with a reset
  and two polls. Eight real bugs have reported success while corrupting data.
- **Precision over recall.** A missing link is a gap; a false link suppresses
  a real outage.
- **Push after every real change.** The repo exists because not having one
  cost a session.
- Ports bind to `127.0.0.1`. Reach the database from the laptop over an SSH
  tunnel, not by exposing it.
- Prefer direct, clear language. Challenge assumptions rather than agreeing;
  say when something is wrong and why.

---

## Open questions

1. Cross-device FDB correlation for the last 2 links, accepting precision
   risk?
2. Alert thresholds — static, or baselined per interface?
3. FortiGate/FortiSwitch at a friend's lab: FortiLink or standalone mode, and
   does `lldpRemLocalPortNum` come back non-zero on FortiOS? (A
   FortiLink-managed switch may not be independently SNMP-pollable at all.)
   Authorization confirmed; reachability is the open problem — this is the
   scenario-2 test case.
4. Retention: how long do metrics and evidence live before rollup? One cycle
   writes ~400 `metric_sample` rows on a 14-device fleet; at 5-minute
   intervals that is ~115k rows/day. Stops being hypothetical the moment the
   timer is enabled.
5. Is the air-gapped variant of scenario 1 a market worth targeting? It
   changes model updates, licensing, and requires a sanitized diagnostics
   export as a shipped feature.
6. `vendor` is still inferred client-side, though `sys_object_id` and
   `sys_descr` both travel on the wire. Strictly a conclusion, not an
   observation. Moving `infer_vendor()` server-side would let detection
   improvements reach already-deployed collectors — worth deciding before
   Phase 6.2 ships collectors you cannot easily update.
7. `sim/walks/optiplex_real.snmpwalk` is stale (6 interfaces vs 7 live), so
   each poll marks the other's extra interface stale. Harmless now; in Phase 3
   an interface flapping state every poll is a false-alert generator.
8. All simulated devices share `mgmt_ip = 127.0.0.1`, so every poll logs 14
   identity-reassignment lines. Harmless — `mgmt_ip` cannot resolve identity —
   but it buries the case where a management IP genuinely moves. Give the
   simulated fleet distinct fake IPs.
