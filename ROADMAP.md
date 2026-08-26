# Sovereign NMS — Status and Roadmap

Scope: deployment scenarios 1 (single-node on-prem) and 2 (distributed
on-prem). Hosted/multi-tenant remains architecturally possible but is
not built toward.

---

## The claim this project has to earn

> Your monitoring tool knows your devices. It doesn't know your network.
> When a switch fails at 2am, it sends you forty alerts instead of one,
> because nobody has kept the dependency map current since 2023.
>
> We discover the topology and rebuild it continuously — no manual
> dependency configuration. So when something breaks, you get root
> cause, not alert spam. And you can ask in plain language, with the
> answer grounded in what we actually observed on the wire.

Everything below either serves that claim or should be cut.

---

## Built and measured

**Schema** — 6 migrations applied (001–006).
`device`, `device_identity`, `interface`, `link`, `link_evidence`,
`dependency`, `metric_sample` (Timescale hypertable), `discovery_run`,
`collector_key`, `device_reachability`, `device_reachability_change`,
plus the `interface_rate` view. Multi-tenant and site-scoped from the
first row.

Design decisions that survived contact with real hardware:

- interfaces key on `(device_id, if_name)`, never `ifIndex`
- identity split into *resolving* (serial, chassis, MAC, sysName) and
  *corroborating* (mgmt IP) — a shared IP must never merge two devices
- links are undirected physical facts; dependency direction is a
  separate derived layer with its own confidence
- links carry fidelity: `interface` (suppression-capable) or `device`
- confidence is computed from evidence, never written directly

**Collector** — Python, 7 modules.
SNMP transport with per-device credentials and timeout isolation ·
IF-MIB parsing · identity resolution · LLDP-MIB parsing across
incompatible vendor implementations · strict FDB leaf-port inference ·
link building with evidence rollup.

**Ingest API** — FastAPI. Collectors post observations; the server owns
all resolution and inference. Tenant is derived from the collector's
credential and never accepted from a payload.

**Test rig** — 14-device simulated fleet generated from a ground-truth
file, with transitive FDB, realistic bridge-port numbering, deliberate
LLDP gaps, an unmanaged switch, and a partial-LLDP device modelled on
real RouterOS behaviour. Plus a scorer that grades discovery against
truth.

**Real hardware** — OptiPlex 5060 (Ubuntu 26.04, Docker, Postgres 16 +
TimescaleDB) · MikroTik RB951G upgraded 6.30 → 6.49.20 with LLDP and
SNMP reachable through a scoped firewall rule.

### Measured result

| Metric | Value |
|---|---|
| Recall | 88.9% (16/18) |
| **Precision** | **100%** |
| Port pairs correct | 16/16 |
| Recall on LLDP-reachable links | 100% |

The 2 missed links are switch-to-switch behind transit ports. Reaching
them needs cross-device FDB correlation, which is where precision
typically dies. 89% with zero false links is a stronger position than
95% with one.

### Real-hardware result

Three real links discovered: OptiPlex → eero GTW, MikroTik → eero 2 AP,
MikroTik → MateBook dock. All three correct, zero false links, device
fidelity throughout — expected, since RouterOS 6.49 reports
`lldpRemLocalPortNum = 0` for every neighbour. Both eeros advertise
`SysName: eero`; before the generic-sysName identity fix, they would
have merged into one device row.

### Bugs the scorer caught that produced no error

1. All 13 simulated devices merged into one, via a shared `mgmt_ip`
2. `base_mac` selecting a Docker-generated (locally administered) MAC
3. One cable stored as two link rows at different fidelities
4. Generator FDB not transitive — inference looked easy
5. Generator bridge ports numbered identically to `ifIndex`
6. Generator learning all of a neighbour's MACs instead of the facing port

Every one of these reported success while writing wrong data. This is
the argument for the scorer existing before the GUI.

---

# Roadmap

Ordered by dependency. Each step states its exit test.

## Phase 1 — Close the loop (unblocks everything)

### 1.1 Collector ingest client
Collector currently writes straight to Postgres. Serialize what
`poll_one` already parses into a `RunPayload` and POST it. Keep a
`--direct` mode for local debugging.

*Exit:* API and direct modes produce identical scorer output and identical
link content — normalise the device pair before diffing, since canonical
column order is UUID-derived and varies between rebuilds.

**Done.**

### 1.2 Scheduler
Polls run when someone types a command. Needs systemd timer or an
internal loop, with jitter so 50 collectors don't stampede.

*Exit:* topology stays current for 24h unattended.

**Done 2026-08-22 — met 5/6, with one recorded exception.** 546
`discovery_run` rows against 273 journal cycles × 2 inventories, zero failed
cycles, scorer baseline held. One stale link, attributed to the MateBook dock
leaving the network; the pass condition was mis-specified and is amended in
`HANDOFF.md`, where the full record lives.

### 1.3 Counter deltas
`metric_sample` holds raw counters. Rates are derived from consecutive
samples on the read path.

**The 32-bit wrap clause that stood here was wrong; corrected 2026-08-25.**
The collector has always polled the 64-bit `ifHCInOctets`/`ifHCOutOctets`
(`1.3.6.1.2.1.31.1.1.1.6` and `.10`). A 32-bit octet counter wraps in about 34
seconds at 1 Gbps, so at a 5-minute interval two samples cannot tell one wrap
from nine, and "handling" it would mean guessing a multiplier — a fabricated
measurement of exactly the kind this project refuses elsewhere. What genuinely
needs handling is a counter reset, and separating a reboot from an agent
anomaly needs `sysUpTime`, not arithmetic.

*Exit:* interface utilisation renders correctly across a device reboot.

**Done 2026-08-25.** `interface_rate` (migration 006, `e85dd81`) computes rate
and utilisation on the read path from raw counters; `sys_uptime_ticks` is
persisted per device (`ecc4362`) and serves as the time base, keeping collector
delay and clock skew out of the denominator. Verified by rebooting
`rb951g-lab`: the sample pair spanning the reboot returned `uptime_reset` with
a null rate on all 14 rows, instead of the -230 Mbps reading the raw delta
would have produced. Rate is emitted for every interface; utilisation only
where a physical denominator exists — a bridge has no link speed and loopback
reports a fictional one. Caveats and measurements in `HANDOFF.md`.

### 1.4 Real device in the pipeline
MikroTik is configured but in no inventory. Add it and the OptiPlex.

*Exit:* one real link discovered between two real devices.

**Done.**

---

## Phase 2 — Dependency direction (**the pitch**)

The hardest and most valuable phase. Discovery says what connects to
what; suppression needs what depends on what.

### 2.1 Direction inference
Derive upstream/downstream per interface-level link from default route
distance, gateway hop count, spanning-tree root, device role, and
traffic asymmetry. Each method scores independently; `dependency.method`
records which contributed.

### 2.2 Direction scorer
Extend the topology scorer to grade direction against ground truth
roles. **Report false-direction rate separately from recall** — a wrong
direction is worse than a missing one.

### 2.3 Threshold calibration
`dependency_trusted` hardcodes 0.80. Derive it empirically: the lowest
threshold at which false suppressions are zero.

*Exit phase:* ≥90% direction accuracy, zero false suppressions on the
ground-truth fleet.

---

## Phase 3 — Alerting and suppression

Five requirements below were derived by writing the Phase 5 read contract
(`interface-design.md`) before this phase rather than after. Each is cheap
while these tables are unwritten and expensive once they hold data.

### 3.1 State change detection
Interface up/down, device unreachable, threshold breach. Distinguish
*unreachable* from *reachable but SNMP-filtered* — a firewall rule
already produced a phantom outage in this lab, and reporting it as a
device failure is the exact false alarm the pitch promises to remove.

- **Alerts key on `device_id`, not `poll_target`.** `optiplex` and
  `optiplex-replay` are two `device_reachability` rows against one device.
  Without aggregation, one device unreachable on one management path
  raises two alerts — the failure mode this product sells against.
- **Bookkeeping churn — cleared 2026-08-24, prerequisite met.** Two
  generators were named here: the "stale" recorded walk (open question 8)
  and lldpd's Docker `veth*` ports (question 11). They were one problem —
  container plumbing recorded as network infrastructure — and
  `store.is_ignored_ifname()` in `c742b30` drops those names before any
  interface row is written. Verified: 226 active / 0 stale across two
  consecutive polls of the same device, scorer unchanged at 88.9% / 100%.
  3.1 no longer inherits ~288 spurious transitions a day. The rule that
  earned this stays: an alerting engine whose first act is to manufacture
  false alarms discredits the claim it exists to prove.

### 3.2 Correlation window
Collapse alerts arriving within N seconds along a dependency chain into
one root-cause incident.

- **`incident` is a first-class row, not a view over alerts.** It needs
  `opened_at`, `closed_at`, `root_cause_device_id`,
  `root_cause_interface_id`, `root_cause_confidence`, `method[]`, `state`.
  A view cannot hold a root cause that was true at 02:14 and is no longer
  true at 07:00, and the 07:00 reading is the one that matters.

### 3.3 Suppression with audit
Every suppressed alert records why, which dependency, and at what
confidence. Non-negotiable: "the tool decided" is not an answer at 2am.

- **`confidence_at_decision` and `threshold_at_decision` are snapshots
  written at suppression time, never a join to the live `dependency` row.**
  Confidence is computed from evidence and recomputed continuously; an
  audit trail that re-derives its reason at read time is not an audit
  trail, it is a guess about the past that will eventually disagree with
  the journal.
- **Decide `discovery_run.started_at` here** — populate it server-side at
  request receipt, or drop it as a false measurement. See open question 10.
  Until then run duration is unmeasurable from the database, so a collector
  slowing toward its interval is invisible until it starts missing cycles.

*Exit phase:* simulated core-switch failure yields **1 incident, not
40 alerts**, with a correct root cause and a full audit trail.

**This is the demo.** Everything before it is groundwork; everything
after is presentation.

---

## Phase 4 — Syslog and the AI layer

The LLM decides nothing. Discovery, direction and suppression stay
deterministic and auditable.

### 4.1 Syslog ingestion
rsyslog → collector → normalized `log_event` table, correlated to
device entities. **Nothing collects logs today**, and this is where the
LLM earns its place: syslog is unstructured vendor prose, which is
exactly what language models are for and what regex never generalises
across FortiOS, NX-OS and RouterOS.

### 4.2 Ollama + grounding contract
Qwen3 8B / Phi-4-mini, Q4_K_M, on the OptiPlex. Model sees only
retrieved rows, cites entity IDs, cannot answer from training
knowledge. Refusing to answer is a valid, expected output.

### 4.3 NLQ over a fixed query set
Model selects and parameterises from a closed set of graph queries. It
never writes SQL and never touches the database.

### 4.4 Incident narration
Given a graph slice and alert timeline, write the human explanation.
The finding is deterministic; only the telling is generated.

*Exit phase:* every NLQ answer traces to specific rows, and unanswerable
questions are refused rather than fabricated.

---

## Phase 5 — Interface

Deliberately late. A GUI built earlier would have displayed six
interfaces on one device, then invited placeholder data.

### 5.1 Read API completion
`/topology` exists. Needs device detail, interface metrics, incidents,
evidence trail.

### 5.2 Topology view
Rendered from the inferred graph. **Fidelity and confidence must be
visible** — a device-level link and an interface-level one are not the
same claim and must not look identical.

### 5.3 Dashboard, incidents, NLQ panel
Health, alerts, root cause with its audit trail.

### 5.4 Self-correction demo
Move a cable, poll again, watch the graph correct itself with nobody
editing anything. Continuous rediscovery is the pitch; a first poll that
is wrong and a second that fixes it never was.

---

## Phase 6 — Deployable product

### 6.1 Installer
One command from bare Ubuntu to running stack.

### 6.2 Site collector package
Thin collector installable at a remote site with only a URL and a token.
Completes scenario 2.

### 6.3 Air-gapped bundle
Signed offline tarball: vendored wheels, container images as `.tar`,
model weights. No network at deploy time. Plus a **sanitized
diagnostics export** — with no field telemetry, it is the only way to
debug a customer problem, and redaction cannot be retrofitted.

### 6.4 Operational hardening
TLS, credential rotation, backup/restore, retention policy on evidence
and metrics.

---

## Critical path to a demo

**Phases 1 → 2 → 3.** That yields alert-storm-to-root-cause on the
simulated fleet, which is the sellable claim. Phase 4 makes it an AI
product; phase 5 makes it watchable; phase 6 makes it shippable.

If time is short, cut phase 4 before phase 3. An NLQ box over a
topology that cannot suppress alerts is a demo of a chatbot. Suppression
without NLQ is still a product.

---

## Explicitly not building

- CNN-LSTM attack classification and the 99.2% figure — an IDS metric
  on a monitoring product, and the wrong claim to defend
- 70B reasoning model — 8B plus grounding does every job identified
- Multi-tenant hosting — a different business with different liabilities
- A from-scratch collector for platforms that already have one; API
  integration with Zabbix/LibreNMS is a later adapter, not a rewrite

---

## Open decisions

1. Cross-device FDB correlation to chase the last 2 links, accepting
   precision risk?
2. Alert thresholds — static, or baselined per interface?
3. FortiGate/FortiSwitch access: which mode is the switch in, and does
   `lldpRemLocalPortNum` come back non-zero on FortiOS?
4. Retention: how long do metrics and evidence live before rollup?
