-- ============================================================
-- 005: per-target reachability, independent of identity resolution
--
-- process_run() used to do `if not obs.reachable: continue` and throw
-- away reach_status. Once Phase 1.2's scheduler runs unattended, that
-- is history nobody can recover: "was this device unreachable or
-- filtered at 03:00?" becomes unanswerable, and that distinction is
-- what Phase 3.1 is built on.
--
-- Keyed on (tenant_id, poll_target), NOT device_id. An unreachable
-- observation carries only the inventory name — identity resolution
-- needs a successful poll, so a target never reached has no device
-- row, and one that has been reached is keyed on sys_name, not the
-- inventory name that got it polled. device_id is filled in once
-- (and if) the target resolves.
-- ============================================================

CREATE TYPE reach_status_kind AS ENUM ('ok', 'unreachable', 'filtered');

-- Current state, one row per polled target. Updated every run.
CREATE TABLE device_reachability (
    tenant_id        UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    poll_target      TEXT NOT NULL,
    device_id        UUID REFERENCES device(device_id) ON DELETE SET NULL,

    reach_status     reach_status_kind NOT NULL,
    error            TEXT,

    last_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- When reach_status last differed from the previous poll. Distinct
    -- from last_attempt_at, which moves on every poll regardless.
    changed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (tenant_id, poll_target)
);

CREATE INDEX ON device_reachability (device_id);
CREATE INDEX ON device_reachability (tenant_id, reach_status);

-- Append-only. One row ONLY when reach_status differs from what was
-- last stored (including the first time a target is ever seen) — not
-- one row per run. At 288 cycles/day across 500 devices, per-run rows
-- would be 144k/day of near-identical data; alerting needs state
-- changes, not samples, and flapping is only visible in transitions.
CREATE TABLE device_reachability_change (
    change_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id      UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    poll_target    TEXT NOT NULL,
    device_id      UUID REFERENCES device(device_id) ON DELETE SET NULL,

    reach_status   reach_status_kind NOT NULL,
    error          TEXT,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON device_reachability_change (tenant_id, poll_target, changed_at DESC);
CREATE INDEX ON device_reachability_change (device_id, changed_at DESC);
