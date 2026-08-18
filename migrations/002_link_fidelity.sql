-- ============================================================
-- 002: links carry fidelity
--
-- Driven by real hardware, not theory. A MikroTik RB951G running
-- RouterOS 6.49 returns lldpRemLocalPortNum = 0 for every neighbour:
-- it reports WHO is adjacent but not on WHICH PORT. The original
-- schema demanded two interface endpoints, so that adjacency was
-- unrecordable even though it was correctly discovered.
--
-- The same shape appears for: neighbours that cannot be polled at all
-- (an eero mesh AP advertises LLDP but serves no SNMP), CDP, ARP and
-- route evidence, and any neighbour named before it has been polled.
--
-- Resolution: one relationship, two fidelities.
--   'interface' — both endpoints known. Usable for suppression.
--   'device'    — devices adjacent, ports unknown. NOT usable for
--                 suppression; "something is adjacent" is not a
--                 dependency.
-- ============================================================

-- A device known only from someone else's advertisement. It is real
-- and it is adjacent, but it has never answered us directly. Kept
-- separate from 'active' so it is never counted as monitored and
-- never generates an alert.
ALTER TYPE entity_state ADD VALUE IF NOT EXISTS 'unpolled';

CREATE TYPE link_fidelity AS ENUM ('interface', 'device');

ALTER TABLE link
    ADD COLUMN device_a UUID REFERENCES device(device_id) ON DELETE CASCADE,
    ADD COLUMN device_b UUID REFERENCES device(device_id) ON DELETE CASCADE,
    ADD COLUMN fidelity link_fidelity NOT NULL DEFAULT 'device';

-- Interface endpoints become optional; the device pair does not.
ALTER TABLE link ALTER COLUMN endpoint_a DROP NOT NULL;
ALTER TABLE link ALTER COLUMN endpoint_b DROP NOT NULL;

-- The old ordering/uniqueness guarantees were expressed on interfaces.
-- They move to the device pair, which is always present.
ALTER TABLE link DROP CONSTRAINT IF EXISTS link_check;
ALTER TABLE link DROP CONSTRAINT IF EXISTS link_endpoint_a_endpoint_b_key;

-- link is empty at this point in the project's life; if it were not,
-- backfill would go here before the NOT NULLs below.
ALTER TABLE link ALTER COLUMN device_a SET NOT NULL;
ALTER TABLE link ALTER COLUMN device_b SET NOT NULL;

-- Canonical ordering on the device pair prevents the same physical
-- adjacency being stored twice depending on which end reported first.
ALTER TABLE link ADD CONSTRAINT link_device_order CHECK (device_a < device_b);

-- Endpoints, when present, must belong to their side of the pair.
-- Enforced in application code (needs a join); documented here.

-- Interface fidelity requires both endpoints. Device fidelity requires
-- neither. A half-populated link is a bug, not a state.
ALTER TABLE link ADD CONSTRAINT link_fidelity_consistent CHECK (
    (fidelity = 'interface' AND endpoint_a IS NOT NULL AND endpoint_b IS NOT NULL)
 OR (fidelity = 'device'    AND endpoint_a IS NULL     AND endpoint_b IS NULL)
);

-- NULLS NOT DISTINCT (PG15+) makes two device-level rows for the same
-- pair collide, which is what we want: one "these are adjacent" row,
-- plus a distinct row per confirmed cable.
ALTER TABLE link ADD CONSTRAINT link_pair_unique
    UNIQUE NULLS NOT DISTINCT (device_a, device_b, endpoint_a, endpoint_b);

CREATE INDEX ON link (tenant_id, device_a);
CREATE INDEX ON link (tenant_id, device_b);
CREATE INDEX ON link (tenant_id, fidelity);

-- Only interface-fidelity links can support dependency direction, and
-- therefore alert suppression. Making that a view rather than a
-- convention means it cannot be forgotten at query time.
CREATE OR REPLACE VIEW link_suppressible AS
SELECT * FROM link
 WHERE fidelity = 'interface'
   AND state = 'active'
   AND confidence >= 0.70;
