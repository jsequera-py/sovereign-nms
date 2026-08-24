# Interface Design — Phase 5 IA and Read-API Contract

Written 2026-08-21. Companion to `ROADMAP.md` §5 and `HANDOFF.md`.

**Status: proposal, not implemented.** Nothing here has been checked against
`server/app.py` — this session cannot reach `192.168.4.181`. Every endpoint
below is either an extension of a known one (`/v1/topology`, `/v1/health`) or
new. Reconcile against the live code before writing any of it.

The reason this exists now, ahead of Phase 5, is not to start drawing screens.
It is that **the read contract constrains Phase 3's schema**, and Phase 3 is
next-but-two. Deciding what the interface must be able to prove is cheap now
and expensive after the alerting tables are written.

---

## 1. Premises

### 1.1 This is not a single pane of glass

The phrase describes an *aggregation* goal. This product's claim is
*reasoning* — one incident instead of forty alerts, with root cause. A
dashboard that aggregates cannot make that claim; it can only display the
forty. The industry critique is well documented: dashboards aggregate data,
they do not reason about it, and unattended ones become wallpaper.

**Consequence.** The home screen is an incident list, not a metrics wall.
Topology, device detail, and metrics are reached *from* an incident. A NOC
wall-display mode may exist later; it is a separate, read-only, deliberately
impoverished view, and it is not the product.

Keep Grafana for what the project profile already assigns it — host,
container, and (later) inference-server health. It cannot render an incident
with a dependency chain and an evidence trail, and trying to make it do so
will cost more than the app. **Two surfaces, drawn on purpose.**

### 1.2 The topology map is not the home screen

Empirical thresholds: shortest-path tasks on node-link diagrams degrade
sharply above ~50 nodes in dense graphs and ~100 in sparse ones, measured with
EEG and pupillometry, not opinion. The sizing target for this product is 500
devices.

**Consequence.** The default topology render is a **slice** — the dependency
chain implicated in one incident, typically 3–8 nodes. The full map is an
explicit mode with grouping (by site, then by role) and filtering, and it is
never the first thing anyone sees. A force-directed full-fleet hairball is a
screenshot for a deck; it is not an operator tool.

### 1.3 Nothing renders without its age

This is the load-bearing rule, and it comes straight from the open finding in
`HANDOFF.md`: topology does not decay without a poll, so a 13-hour-old graph
currently renders identically to a live one. `minutes_since_last_run` is the
only signal and nothing surfaces it.

**A UI built on today's read path would ship a lie.** Not a bug — a lie, in
the specific sense that a monitoring tool showing a healthy green graph from
dead data is worse than showing nothing.

**Consequence.** Freshness is computed on the *read* path, per §4, and every
response carries it. Staleness is a function of time, and time passes whether
or not anything polls.

### 1.4 Three attributes travel with every claim

`ROADMAP.md` §5.2 already requires fidelity and confidence to be visible. Add
evidence age. A `device`-fidelity link at 0.65 last seen 40 minutes ago and an
`interface`-fidelity link at 0.80 seen 2 minutes ago are not the same claim and
must not render alike — different stroke, different label, not merely a
different tooltip.

---

## 2. Screen inventory

| # | Screen | Purpose | Blocked on |
|---|---|---|---|
| 1 | **Incidents** (home) | Open incidents, root cause, blast radius | Phase 3 |
| 2 | **Incident detail** | Timeline, graph slice, evidence, suppression audit | Phase 3 |
| 3 | **Devices** | Fleet table, reachability incl. `filtered` | now |
| 4 | **Device detail** | Interfaces, metrics, identity claims, links | 1.3 for rates |
| 5 | **Topology** | Full map, grouped and filtered | now |
| 6 | **Collector health** | Runs, cycle spacing, per-inventory outcome | q.10 (`started_at`) |
| 7 | **Ask** | NLQ with citations; refusal is a valid render | Phase 4 |

Screens 3, 5 and 6 are buildable against data that exists today. They are the
honest starting point, and none of them needs placeholder numbers.

**Screen 3 carries a differentiator cheaply.** `reach_status` distinguishes
`unreachable` from `filtered` — a device that answered but restricted the view.
The lab already produced this: a correctly configured firewall made the
MikroTik look dead. Every competing tool shows one red dot for both. Showing
"SNMP filtered — device is up, view restricted" on day one is a visible win
that costs a badge.

### Empty vs degraded vs healthy

Three distinct states, three distinct renders. "No incidents" (good), "no data"
(collector dead), and "data older than the freshness window" (degraded) must be
impossible to confuse. Most tools collapse the first two into an empty list,
which is how a dead collector reads as a quiet night.

---

## 3. Read-API contract

Additive to `/v1/ingest/run`. Same bearer credential; tenant and site continue
to derive from the credential and are never accepted from a request — the wire
contract's one non-negotiable property applies to reads too.

### 3.0 Envelope — on every response

```json
{
  "as_of": {
    "generated_at": "2026-08-21T20:44:03Z",
    "last_run_at": "2026-08-21T20:41:18Z",
    "age_s": 165,
    "poll_interval_s": 300,
    "evidence_window_s": 1800,
    "freshness": "fresh",
    "degraded": false
  },
  "data": { }
}
```

`freshness` and `degraded` are computed at request time, never stored. §4 gives
the rule.

### 3.1 `GET /v1/incidents`

```json
{"incidents": [{
  "id": "inc_01J...",
  "state": "open",
  "opened_at": "2026-08-21T02:14:07Z",
  "root_cause": {
    "device_id": "...", "device_name": "dist-sw-01",
    "interface_id": null,
    "confidence": 0.86,
    "method": ["default_route_distance", "stp_root"]
  },
  "blast_radius": {"devices": 12, "interfaces": 27},
  "alerts": {"raised": 1, "suppressed": 39},
  "evidence_age_s": 143
}]}
```

`alerts.raised` / `alerts.suppressed` is the pitch rendered as two integers. It
belongs on the list row, not buried in detail.

### 3.2 `GET /v1/incidents/{id}`

Adds `timeline[]` (state changes in order, each with `observed_at`, `source`,
`device_id`), `graph` (§3.4 shape, scoped to the chain), and:

```json
"suppression_audit": [{
  "alert_id": "...",
  "device_name": "acc-sw-03",
  "condition": "device_unreachable",
  "suppressed_because": {
    "dependency_id": "...",
    "upstream_device": "dist-sw-01",
    "method": ["default_route_distance"],
    "confidence_at_decision": 0.86,
    "threshold_at_decision": 0.80,
    "decided_at": "2026-08-21T02:14:09Z"
  }
}]
```

**`confidence_at_decision` must be a snapshot written at suppression time, not
a join to the current `dependency` row.** Confidence is recomputed from
evidence continuously; if the audit trail re-derives its reason at read time,
it is not an audit trail, it is a guess about the past that will disagree with
the journal. This is a Phase 3 schema requirement, discovered by writing the
read contract — which is the point of writing it early.

### 3.3 `GET /v1/devices` · `GET /v1/devices/{id}`

List: `id`, `name`, `role`, `site`, `vendor`, `reach_status`, `last_attempt_at`,
`changed_at`, `mgmt_paths[]`, `interface_count`, `link_count`.

`mgmt_paths[]` matters: `optiplex` and `optiplex-replay` are two
`device_reachability` rows against one `device_id`. The list must aggregate to
device level — otherwise one device unreachable on one path renders as two
problems, which is the failure mode this product sells against.

Detail adds `interfaces[]`, `links[]` (each with `fidelity`, `confidence`,
`evidence_age_s`), and:

```json
"identity": {
  "resolved_by": "chassis_id",
  "claims": [
    {"type": "chassis_id", "value": "0c:93:...", "first_seen": "...", "last_seen": "..."},
    {"type": "sysname", "value": "rb951g-lab", "first_seen": "...", "last_seen": "..."}
  ],
  "rejected": [{"type": "sysname", "value": "eero", "reason": "generic"}]
}
```

Surfacing `rejected` is not decoration. Two eeros sharing one sysName was a
silent merge waiting to happen, and the veto rule in `identity-design.md` is
about to add more silent decisions. **A rule that fires invisibly cannot be
reviewed.**

### 3.4 `GET /v1/topology`

Params: `scope=incident:{id}` | `site:{id}` | `all`, `min_confidence`,
`fidelity`, `group_by=site|role|none`, `depth` (hops from a seed device).

```json
{"nodes": [{
  "id": "...", "name": "rb951g-lab", "role": "router",
  "reach_status": "ok", "polled": true, "placeholder": false
}],
 "edges": [{
  "id": "...", "a": "...", "b": "...",
  "a_if": "ether1", "b_if": null,
  "fidelity": "device", "confidence": 0.65,
  "sources": ["lldp"],
  "evidence_age_s": 168, "freshness": "fresh",
  "state": "active"
}]}
```

`placeholder: true` for neighbours outside the inventory — the eeros, the
MateBook dock. They are real observations, not devices under management, and
rendering them identically to polled devices overstates coverage.

`scope=all` on a 500-device fleet returns groups, not 500 nodes, when
`group_by != none`. Per §1.2 the ungrouped full map is available but is not a
default anyone falls into.

**Direction is a separate array, never an attribute of an edge.** Links are
undirected physical facts; dependency direction is a derived layer with its own
confidence. Collapsing them into a directed edge in the JSON would erase that
line at exactly the point where the UI is most tempted to draw an arrow. So:

```json
"dependencies": [{
  "upstream": "...", "downstream": "...", "link_id": "...",
  "confidence": 0.86, "method": ["stp_root", "default_route_distance"]
}]
```

A link with no dependency row renders as a plain line. A link with one renders
with an arrow *and* the confidence that justifies it. Two layers, two visual
weights.

### 3.5 `GET /v1/links/{id}/evidence`

The trust primitive. One row per `(source, reporter_device)` with `observed_at`,
`age_s`, `fresh: bool`, and the raw values that produced it. Every confidence
number anywhere in the UI is a link to this. Confidence is computed from
evidence and never written directly; the interface should make that checkable
rather than merely stated.

### 3.6 `GET /v1/health` (extend)

Keep `minutes_since_last_run`, `cross_site_links`. Add per-inventory last run
and outcome, cycle count over 1h/24h, observed mean spacing, and
`links_by_freshness: {fresh, aging, stale}`.

That last field is the 24-hour exit test as a live number rather than a manual
psql query.

---

## 4. The freshness rule

Computed on read, per §1.3. `W` = `link_evidence_fresh` (1800s), `P` = poll
interval (300s).

**Per link** — `age = now − max(evidence.observed_at)`:

| age | `freshness` | render |
|---|---|---|
| ≤ W | `fresh` | solid |
| ≤ 2W | `aging` | solid, age label shown |
| > 2W | `stale` | dashed, greyed, confidence shown as *last known* |

**Globally** — `degraded = minutes_since_last_run > 2P` (i.e. >10 min).
When degraded, every screen carries a persistent banner: *"Collector last
reported HH:MM (Xh ago). Everything below is that old."* Not a toast, not a
corner icon — a banner that does not dismiss.

**A stale link keeps its last confidence and loses its authority.** Do not
decay the number: 0.80 was true when measured, and inventing 0.42 to represent
uncertainty fabricates a measurement. Show `0.80 · unverified 13h`.

This resolves `HANDOFF.md` open question 1 in favour of option 2 (staleness on
the read path) for display purposes, without touching whether
`rollup_confidence()` should also change `link.state` on write. Those are now
separable, which they were not before.

---

## 5. Stack

**FastAPI + Jinja2 + HTMX, server-rendered.** Decided against a React SPA.

- Phase 6.3 requires a signed offline bundle. No npm toolchain, no CDN, no
  `node_modules` in the tarball. `htmx.min.js` is ~14KB, vendored into the
  repo and served locally.
- Data changes every 5 minutes. A framework built for optimistic local state
  buys nothing against a 5-minute backend. `hx-trigger="every 30s"` on the
  incident list is the entire live-update story. **No websockets, no SSE** —
  they add a connection lifecycle to debug in exchange for latency nobody
  can perceive at this poll interval.
- CSP-friendly and auditable, which matters for a product sold on sovereignty.

**Graph rendering: server-side SVG via Graphviz for slices.** A 3–8 node
incident chain is `dot` output, rendered by the server, zero JavaScript. Only
if and when the full-fleet interactive map earns its place does a vendored
Cytoscape.js get added — and by then it is one screen's dependency, not the
app's foundation.

**Deliberately not chosen:** Grafana as the app shell (§1.1), any charting
library before Phase 1.3 produces real rates.

**Placement.** The UI is part of `server/`, not `collector/`. The Phase 6.2
thin site collector ships without `server/` and therefore without a UI, which
is correct — a remote site collector is headless and reports to the node that
has the database. This keeps the existing lazy-import arrangement intact.

---

## 6. What this demands of Phase 3

The payoff for writing this now. Each item is cheap while the tables are
unwritten and expensive after.

1. **`suppression_audit.confidence_at_decision` and `threshold_at_decision`
   are snapshots.** §3.2. Do not join to live `dependency`.
2. **Incident is a first-class row**, not a view over alerts. It needs
   `opened_at`, `closed_at`, `root_cause_device_id`, `root_cause_interface_id`,
   `root_cause_confidence`, `method[]`, `state`.
3. **Alerts aggregate to `device_id`, not `poll_target`.** Already flagged in
   `HANDOFF.md`; the device list makes it visible on screen.
4. **`discovery_run.started_at` populated server-side at request receipt**
   (open question 10). Without it screen 6 cannot show run duration, and a
   collector slowing toward its interval stays invisible until it starts
   missing cycles.
5. **Interface churn must be distinguishable from interface state change**
   before any UI shows interface status. The stale recorded walk (7 live
   interfaces vs 6 recorded) and lldpd's Docker `veth*` ports both generate
   ~288 spurious transitions a day. On screen that is a flapping red dot that
   is entirely an artifact — the exact false alarm the pitch promises to
   remove, generated by our own bookkeeping.

---

## 7. Open questions

1. Does `link.state` ever become `stale` on the write path, or is §4's read-path
   computation the only staleness that exists? They can coexist; they must not
   disagree on screen.
2. Incident list default scope — open only, or open + resolved-in-last-24h? The
   second is what you actually want at a 07:00 handover.
3. Does the NLQ panel (screen 7) share the incident detail view, or is it a
   separate screen? Sharing it makes citations concrete — the model's answer
   points at rows already rendered beside it.
4. Authentication for the read UI. The ingest bearer token is a collector
   credential; a human session is a different thing and is currently unbuilt.
   Nginx basic auth in front is the project profile's answer, and it is
   probably sufficient for scenarios 1 and 2 — but it means no per-user audit
   of who acknowledged what, which Phase 3 may want.

---

## 8. Visual language — `design/`

Six artboards and a canvas manifest, committed 2026-08-22 in `159362e`:

| File | Carries |
|---|---|
| `design/Main.dc.html` | trust primitives — the confidence, fidelity and freshness marks |
| `design/Foundations.dc.html` | type scale, colour roles |
| `design/States.dc.html` | reachability states, degraded banner, the three empty states |
| `design/Incidents.dc.html` | incident home screen — screen 1 |
| `design/Topology.dc.html` | graph slice — screen 5 |
| `design/Devices.dc.html` | fleet table — screen 3, and per `canvas.json` the one screen buildable against data that exists today |
| `design/canvas.json` | artboard layout manifest |

Plain HTML with inline styles and inline SVG, deliberately: close to liftable
into the Jinja templates §5 commits to, with no build step between the picture
and the page.

**Two encoding rules, and they are independent channels.**

1. **Fidelity rides terminal construction; freshness rides dash pattern.**
   Interface fidelity closes on both nodes with named ports; device fidelity
   has open terminals and a gap, because we know the boxes and not the ports.
   Freshness is carried by the stroke pattern, per §4. The two never share a
   channel, so all six combinations stay readable — a stale interface-fidelity
   link and a fresh device-fidelity one must not converge on the same mark.
2. **Four reachability states, not three.** `filtered` gets its own hue and
   glyph. A device that answered and restricted the view is not an outage, and
   every competing tool draws one red dot for both. This is §2's cheap
   differentiator made visible.

**Implementation note, from the 2026-08-22 exit test.** The artboards render a
stale link as `0.80 · unverified 30h`, per §4. That number cannot come from
`link.confidence` — the rollup zeroes it when evidence ages out, confirmed on
`rb951g-lab ↔ 64:c9:01:a9:42:7e`. Compute it from `link_evidence`, which
retains `observed_at` and `raw_claim` after the rollup has run. Wiring the
template to the column is the obvious mistake, and it renders
`0.00 · unverified 30h` — a lie about a measurement that was actually taken.

**The published canvas artifact is derived, not canonical.** It predates these
files and carries contradictions they fixed. `design/` at HEAD is the source;
the artifact gets republished from it, and until that happens it should not be
read.

---

## Sources

- Kentik — [The "Single Pane of Glass" Is Dead](https://www.kentik.com/blog/the-single-pane-of-glass-is-dead-what-network-teams-actually-need-is-intelligence/)
- BETSOL — [The Myth of the "Single Pane of Glass"](https://www.betsol.com/blog/the-myth-of-the-single-pane-of-glass/)
- Yoghourdjian et al. — [Scalability of Network Visualisation from a Cognitive Load Perspective](https://arxiv.org/abs/2008.07944) (IEEE TVCG)
- Cambridge Intelligence — [Graph visualization UX](https://cambridge-intelligence.com/graph-visualization-ux-how-to-avoid-wrecking-your-graph-visualization/)
- INOC — [NOC Dashboards: Pursuing the Single Pane of Glass](https://www.inoc.com/blog/noc-dashboards)
