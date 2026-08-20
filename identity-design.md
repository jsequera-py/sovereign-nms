# Identity resolution — closing the sysName merge hole

Design note, 2026-08-21. Needs settling before Phase 2, because dependency
direction is only as good as the device rows it points at.

Supersedes the "learn generic names over time" suggestion from the previous
session. Reading `resolve_device()` showed a cleaner fix.

---

## How the merge actually happens

Trace two eeros through `resolve_device()`:

**Device A — eero GTW.** Claims: `chassis_id=30:34:22:d7:1b:00`,
`sysname=eero`. Nothing matches, so a device is created and both claims are
attached.

**Device B — eero 2 AP.** Claims: `chassis_id=0c:93:a5:24:86:e0`,
`sysname=eero`.

- `chassis_id=0c:93…` matches nothing — this hardware has never been seen
- `sysname=eero` matches **device A**
- `matched = {A}` — exactly one device

`len(matched) > 1` is false, so the "manual merge required" warning at line 87
never fires. The function resolves to A, and B is absorbed. The claim-
attachment loop then hangs `chassis_id=0c:93…` onto A, so A now carries two
distinct chassis identifiers.

The reassignment warning at line 132 doesn't fire either, because `sysname`
didn't move — it stayed on A the whole time.

**So the merge is invisible to all three existing safeguards.** The multi-match
warning needs two matches and there is one. The reassignment warning needs an
identifier to move and none does. The `RESOLVING_TYPES` guard is satisfied,
because `sysname` is a resolving type.

---

## The rule

> **An unmatched identifier vetoes a weaker matched one.**
>
> If the claim set contains a resolving identifier that matches nothing, and
> it is *stronger* than the strongest identifier that did match, this is a new
> device. Do not resolve to the match.

In the eero case: `chassis_id` (strength 2 in `IDENTITY_PRECEDENCE`) is
unmatched; `sysname` (strength 4) matched. The unmatched one is stronger, so
device B is created rather than absorbed.

The reasoning is plain. A chassis identifier nobody has ever seen means
hardware nobody has ever seen. What its sysName claims is irrelevant — a name
cannot outrank a serial number.

### Why this is better than learning names over time

The learned rule I proposed yesterday — *a sysName seen against two chassis is
not an identity* — has three problems this one avoids:

1. **It fires too late.** The name is only known to be ambiguous *after* the
   second device shows up, and by then the merge has already happened. Undoing
   a merge means splitting history apart, which is far harder than not merging.
2. **It needs new state** — a table of name-to-chassis observations, per
   tenant, maintained forever.
3. **It punishes the case sysName exists for.** Replace a failed switch and
   keep its name: same sysName, new chassis. A learned denylist demotes that
   name permanently, destroying exactly the continuity sysName is meant to
   provide.

The veto rule is local, needs no history, and decides at the only moment that
matters — before the merge.

It also handles the RMA case honestly rather than silently: a new chassis
becomes a new device row. That is arguably correct (it *is* new hardware), and
if operators want continuity across a swap, that should be an explicit
merge action with an audit trail, not an accident of naming.

---

## Check it against the cases that must keep working

| Scenario | Claims | Matched | Unmatched stronger? | Outcome |
|---|---|---|---|---|
| Same device re-polled | chassis ✓, sysname ✓ | A | no | resolves to A ✓ |
| Second eero | chassis ✗, sysname ✓ | A | **yes** | new device ✓ |
| MikroTik (no chassis_id at all) | base_mac ✓, sysname ✓ | A | no | resolves to A ✓ |
| NIC added — new MAC alongside old | base_mac ✓ and ✗, sysname ✓ | A | no (same tier) | resolves to A ✓ |
| Genuinely new device | nothing matches | — | n/a | new device ✓ |
| Hardware swap, name kept | chassis ✗, sysname ✓ | A | yes | new device — see above |

The fourth row is the one to be careful about. Interface churn produces
unmatched `base_mac` claims all the time, and those must not trigger the veto.
"Strictly stronger" does the work: an unmatched `base_mac` never outranks a
matched `base_mac`, because they are the same tier.

---

## What `GENERIC_SYSNAMES` is still for

The veto only helps when a stronger identifier is present. Two devices whose
*only* resolving claim is `sysname=eero` — no serial, no chassis, no hardware
MAC — would still merge.

That is a device announcing nothing but a product name, and the denylist is
the right floor for it. Keep it, keep it in `store.py`, keep adding names as
they turn up. It stops being the primary defence and becomes the backstop.

---

## Separate hole found while reading this: identity is not site-scoped

`resolve_device(conn, tenant_id, claims, …)` looks up `device_identity` by
tenant only. `site_id` is applied *afterwards*, from the collector credential.

So two sites in one tenant, each with a switch called `core-sw-01`, resolve to
**one device**. Same failure as the original shared-`mgmt_ip` bug, reached by a
different road — and it lands squarely on deployment scenario 2, which is
distributed on-prem by definition.

A defensible split:

- **Globally unique identifiers** — `serial`, `chassis_id`, `base_mac` — stay
  tenant-scoped. A MAC is a MAC anywhere.
- **Locally unique identifiers** — `sysname` — resolve **within a site only**.
  Names are a local naming convention, not a global namespace.

That needs `device_identity` to carry `site_id` for site-scoped types, and the
lookup to filter on it. Worth doing at the same time as the veto rule, since
both touch the same query and both are about not trusting names too far.

`/v1/health` already exposes `cross_site_links`, which exists to catch exactly
this class of collision. It has been 0 throughout — but there is only one site
configured, so it has never been tested.

---

## Suggested order

1. **Veto rule** in `resolve_device()`. Small, local, no schema change.
   Verify: reset, poll sim + real, both eeros must remain separate devices
   with `eero` removed from `GENERIC_SYSNAMES` to prove the veto is doing the
   work rather than the denylist. Then put `eero` back.
2. **Site-scoped sysname resolution.** Schema change. Verify with a second
   site containing a deliberately duplicate device name.
3. Leave the denylist alone. It is the floor, not the mechanism.

Step 1 is worth doing before Phase 2. Step 2 can wait until a second site
actually exists — but not until after direction inference is built on top of
possibly-merged devices.

---

## The test that would have caught this

None of the existing verification would fail on a silent merge, because the
scorer only grades the simulated fleet and the simulated fleet has unique
sysNames.

Worth adding to `sim/topology.yaml`: **two devices sharing a factory-default
sysName, with distinct chassis identifiers.** Ground truth says two devices;
if discovery reports one, the scorer catches it. Right now that scenario only
exists in the real lab, where nothing grades it.
