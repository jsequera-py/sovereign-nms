# Sovereign NMS — Session Handoff

Paste this into a new chat, along with `ROADMAP.md`, to resume.

---

## What this is

A network monitoring system whose differentiator is **automatic,
continuously-rebuilt topology discovery** feeding **alert suppression
with root cause**. Deployment scenarios 1 (single-node on-prem) and 2
(distributed on-prem) only.

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

**Working and measured.** Discovery engine complete through FDB
inference. Ingest API built and verified. Nothing consumes the topology
yet — no direction inference, no suppression, no UI, no scheduler.

| Metric | Value |
|---|---|
| Recall | 88.9% (16/18 links) |
| **Precision** | **100%** |
| Port pairs correct | 16/16 |
| Recall on LLDP-reachable links | 100% |

**This baseline must not regress.** Run the scorer after every change
to the collector or the fleet generator.

---

## Environment

**Hardware**
- OptiPlex 5060, Ubuntu 26.04, `192.168.4.181`, user `jsequera`
- MateBook D14 — thin client only, VS Code Remote-SSH into the OptiPlex
- MikroTik RB951G-2HnD — RouterOS **6.49.20** (upgraded from 6.30.4)

**MikroTik details**
- ether1 (WAN, to eero): `192.168.4.182`
- bridge-local: `192.168.88.1`
- SNMP community: `nmslab`
- LLDP enabled, discovery interface list = all
- Firewall: an `input accept` rule for src `192.168.4.181`, udp/161,
  in-interface ether1, placed **above** the default WAN drop rule
- wlan1 disabled
- sysName is still the factory default `MikroTik` — **set System →
  Identity before using it**, or its sysName is unusable as an identifier

**Not usable for monitoring:** eero mesh (cloud-managed, no SNMP —
but it *does* advertise LLDP, so it appears as a neighbour), Cisco X1000
DSL modem, Luxul XGS-1008 (unmanaged — useful only as a physical
"hidden switch" test).

**Stack**
- Postgres 16 + TimescaleDB in Docker (`nms-db`, 127.0.0.1:5432)
- snmpsim in Docker (`nms-snmpsim`, 127.0.0.1:1161/udp)
- Python 3.14 venv at `~/nms/.venv`
- FastAPI + uvicorn

**Credentials (dev only)**
- `DB_URL=postgresql://nms:nms_dev_only@127.0.0.1:5432/nms`
- `TENANT_ID=1e7e1955-3f93-4575-b158-615f00a26e5c` (tenant `lab`)
- Site `home`, collector key `optiplex-01` — token is in `.env` as
  `INGEST_TOKEN`; it cannot be recovered, reissue if lost

---

## Repository layout — `~/nms`

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

### Commit history

```
roadmap
8a71aec ingest API, collector keys, roadmap
07b1bb9 fdb: strict leaf-port inference (89% recall / 100% precision)
23a274c topology: lldp link builder, evidence rollup, scorer (72%/100%)
c1af4da schema: link fidelity; lldp parser for vendor variance
d678cf6 identity: separate resolving from corroborating identifiers
0863327 collector: hardware-MAC identity claims; survives interface churn
2313fb2 collector: identity resolution + IF-MIB
dfbaf1d core schema + compose scaffold
```

**Not yet on GitHub.** Four file-transfer failures happened in one
session because of this. Push to a private repo before doing anything
else.

---

## Running it

```bash
cd ~/nms
docker compose up -d                      # db + snmpsim
.venv/bin/python sim/genfleet.py          # regenerate fleet from ground truth
./scripts/reset_data.sh                   # wipe collected data (keeps schema)
.venv/bin/python -m collector.poll --inventory inventory.generated.yaml
.venv/bin/python -m collector.poll --inventory inventory.generated.yaml   # twice
.venv/bin/python scripts/score_topology.py

# API
DB_URL="postgresql://nms:nms_dev_only@127.0.0.1:5432/nms" \
  .venv/bin/uvicorn server.app:app --host 127.0.0.1 --port 8000
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/v1/health
```

**Two polls are required** on a fresh database: the first creates
placeholder devices for neighbours not yet polled, the second upgrades
those links to interface fidelity. That self-correction is a feature,
and it is the demo.

---

## Architectural decisions (settled — do not relitigate without cause)

**Schema**
- Interfaces key on `(device_id, if_name)`, **never `ifIndex`** —
  ifIndex is reassigned across reboots and on virtual-interface churn
- Links are **undirected physical facts**; dependency direction is a
  separate derived layer with its own confidence
- Links carry **fidelity**: `interface` (suppression-capable) or
  `device` (adjacency known, ports unknown — not suppression-capable)
- Canonical ordering `device_a < device_b` prevents one cable being
  stored twice
- **Confidence is computed from evidence, never written directly.**
  Code that sets `link.confidence` by hand is a bug

**Identity**
- **Resolving** identifiers: serial, chassis_id, base_mac, sysName
- **Corroborating** only: mgmt_ip — recorded, but can never establish
  that two observations are the same device
- Locally-administered MACs (docker0, br-*, veth*) are excluded from
  identity: they regenerate on container restart
- Generic sysNames (`MikroTik`, `switch`, `router`, …) are refused as
  identifiers — they name a product line, not a device

**Evidence**
- One row per `(link, source, reporter_device)`. Re-polling refreshes;
  it does not accumulate. Row count = number of independent confirmations
- Freshness window 30 minutes (`link_evidence_fresh`); must exceed the
  poll interval or every link decays between runs
- Two-sided LLDP → 0.80. One-sided → 0.65. That gap is the model working

**FDB inference is strict**
- A port qualifies only if it shows exactly one known device and
  ≤ `MAX_LEAF_MACS` (6) total MACs
- Finds leaf attachments (hosts, hypervisors, APs); deliberately does
  **not** find switch-to-switch links, where "adjacent" and "reachable"
  are indistinguishable
- Loosening this is how false links appear, and a false link becomes a
  false dependency, which suppresses a real outage

**Tenancy**
- The server derives `tenant_id` from the collector's credential.
  A payload may never state its own tenant
- This one property is what keeps a future hosted model safe

**The LLM decides nothing**
- Discovery, direction and suppression stay deterministic and auditable
- LLM scope: NLQ parameterisation over a fixed query set, incident
  narration, syslog triage, vendor translation
- 8B (Qwen3 / Phi-4-mini Q4_K_M) is sufficient. The 70B requirement from
  the original spec is cut

---

## Vendor realities discovered on actual hardware

**MikroTik RouterOS 6.49 returns `lldpRemLocalPortNum = 0`** for every
neighbour — it reports *who* is adjacent but not *on which port*. It
also omits `lldpLocChassisId` entirely and uses PortId subtype 3
(macAddress), putting the interface name only in PortDesc.

This is why `link` supports device-level fidelity at all. Any parser
assuming complete LLDP-MIB finds nothing on MikroTik, and MikroTik is
not a fringe vendor. The simulated fleet includes a `routeros_partial`
profile reproducing this exactly.

**A correctly-configured firewall makes a device look dead.** SNMP
queries to the MikroTik timed out identically to a powered-off switch.
The collector still cannot distinguish *unreachable* from *reachable
but filtered* — both raise `SnmpError`. Fixing this is Phase 3.1, and it
matters because reporting a filtered device as an outage is precisely
the false alarm the pitch promises to remove.

---

## Bugs that produced no error while writing wrong data

1. All 13 simulated devices merged into one device row, via shared
   `mgmt_ip` — reported `polled 13/13 successfully`
2. `base_mac` selected a Docker-generated MAC that changes on restart
3. One cable stored as two link rows at different fidelities
4. Generator FDB not transitive — inference looked trivially easy
5. Generator bridge ports numbered identically to ifIndex
6. Generator learning all of a neighbour's MACs, not just the facing port

**Every one reported success.** This is why the scorer exists before the
GUI, and why 3–6 matter most: a generator wrong *in your favour* is the
most dangerous failure mode here, because nothing in the pipeline
complains.

---

## Ground truth — `sim/topology.yaml`

14 devices, 18 links. **This file is the answer key.** Editing it to
make discovery look better deletes the only honest measurement in the
project.

| LLDP mode | Count | Expectation |
|---|---|---|
| both | 12 | found trivially |
| one | 1 | found, lower confidence (0.65) |
| none | 4 | needs FDB inference |
| hidden switch | 1 | not findable |

Currently missed: `acc-sw-02 ↔ acc-sw-03` (unmanaged switch between)
and `dist-sw-01 ↔ acc-sw-03` (switch-to-switch, no LLDP). Both would
require cross-device FDB correlation, which risks precision.

---

## Next steps

**Phase 1.1 — collector ingest client** (immediate)
The collector writes directly to Postgres. Serialize what `poll_one`
already parses into a `RunPayload` (`common/wire.py`) and POST to
`/v1/ingest/run`. Keep a `--direct` mode for local debugging.
*Exit test:* poll via API produces identical scorer output to direct mode.

**Phase 1.4 — MikroTik into inventory** (do early)
Ten-minute change. Everything validated so far ran against a generator
written by the same person as the parser. That loop needs breaking
before three more phases are built on top of it.

Then 1.2 scheduler, 1.3 counter deltas, and on to Phase 2 (dependency
direction) and Phase 3 (suppression). Phase 3's exit test — **one
incident instead of forty alerts, with root cause and audit trail** — is
the sellable claim. Full detail in `ROADMAP.md`.

---

## Working rules

- **Run the scorer after every collector or generator change.** Six real
  bugs in one session reported success while corrupting data
- **Precision over recall.** A missing link is a gap; a false link
  suppresses a real outage
- Push to GitHub — VS Code Remote-SSH into the OptiPlex for editing;
  files live and execute on the server
- Ports bind to `127.0.0.1`. Reach the database from the laptop over an
  SSH tunnel, not by exposing it
- Prefer direct, clear language. Challenge assumptions rather than
  agreeing; say when something is wrong and why

---

## Open questions

1. Cross-device FDB correlation for the last 2 links, accepting
   precision risk?
2. Alert thresholds — static, or baselined per interface?
3. FortiGate/FortiSwitch at a friend's lab: is the switch in FortiLink
   or standalone mode, and does `lldpRemLocalPortNum` come back non-zero
   on FortiOS? (A FortiLink-managed switch may not be independently
   SNMP-pollable at all.) Authorization confirmed; reachability is the
   open problem — this is the scenario-2 test case
4. Retention: how long do metrics and evidence live before rollup?
5. Is the air-gapped variant of scenario 1 a market worth targeting?
   It changes model updates, licensing, and requires a sanitized
   diagnostics export as a shipped feature
