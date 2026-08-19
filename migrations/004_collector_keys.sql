-- ============================================================
-- 004: collectors authenticate; tenancy is derived, never declared
--
-- Enables both deployment models from one codebase:
--
--   ON-PREM      customer runs the API and the database. Collectors at
--                each of their sites post to it. Data never leaves
--                infrastructure they own, however many sites there are.
--
--   HYBRID       the API is operated by someone else (an MSP, or you).
--                Multiple unrelated tenants share it.
--
-- The difference is deployment, not code. What makes hybrid SAFE is
-- this table: a collector presents a key, and the server looks up which
-- tenant that key belongs to. The payload never states a tenant_id.
-- If it did, a compromised or buggy collector at one customer could
-- write into another customer's topology.
-- ============================================================

CREATE TABLE collector_key (
    key_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    -- A collector is bound to one site. Two customers both using
    -- 192.168.1.1 must not collide, and neither must two branches of
    -- the same customer.
    site_id       UUID REFERENCES site(site_id) ON DELETE SET NULL,

    name          TEXT NOT NULL,
    -- SHA-256 of the token. The plaintext is shown once at creation and
    -- never stored — a stolen database must not yield working keys.
    key_hash      TEXT NOT NULL UNIQUE,

    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    last_used_ip  INET,

    UNIQUE (tenant_id, name)
);

CREATE INDEX ON collector_key (key_hash) WHERE enabled;

-- Which collector reported a run, for audit and for spotting a site
-- that has stopped reporting.
ALTER TABLE discovery_run
    ADD COLUMN collector_key_id UUID REFERENCES collector_key(key_id) ON DELETE SET NULL,
    ADD COLUMN site_id UUID REFERENCES site(site_id) ON DELETE SET NULL;

-- Devices already have site_id but nothing sets it. With one site that
-- was harmless; with two it is a correctness bug, because topology
-- queries would happily draw links between sites that share nothing.
CREATE INDEX IF NOT EXISTS device_tenant_site_idx ON device (tenant_id, site_id);

-- A link must not span sites. Two devices at different physical
-- locations with a discovered adjacency means either bad data or an
-- identity collision — most likely two customers both using
-- 192.168.1.1. Surfacing it is the point; this view is what a health
-- check reads.
CREATE OR REPLACE VIEW link_cross_site AS
SELECT l.link_id, l.tenant_id,
       da.display_name AS device_a, sa.name AS site_a,
       db.display_name AS device_b, sb.name AS site_b
  FROM link l
  JOIN device da ON da.device_id = l.device_a
  JOIN device db ON db.device_id = l.device_b
  LEFT JOIN site sa ON sa.site_id = da.site_id
  LEFT JOIN site sb ON sb.site_id = db.site_id
 WHERE da.site_id IS DISTINCT FROM db.site_id
   AND da.site_id IS NOT NULL
   AND db.site_id IS NOT NULL;
