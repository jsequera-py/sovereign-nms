-- ============================================================
-- Sovereign NMS — Core Canonical Schema  v0.1
-- Target: PostgreSQL 15+ / TimescaleDB 2.x
--
-- Design rules:
--   1. Physical links are UNDIRECTED facts.
--   2. Dependency direction is a DERIVED inference with confidence.
--   3. Every fact carries provenance. Manual always wins.
--   4. tenant_id everywhere, from day one.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- CREATE EXTENSION IF NOT EXISTS timescaledb;   -- enable for metrics

-- ------------------------------------------------------------
-- ENUMS
-- ------------------------------------------------------------

-- Ordered loosely by trust. Used to resolve conflicting claims.
CREATE TYPE source_kind AS ENUM (
    'manual',        -- operator asserted. Never auto-overwritten.
    'lldp',          -- neighbor discovery, strongest automated signal
    'cdp',
    'api',           -- vendor controller (Meraki, vCenter, SonicWall)
    'mac_table',     -- L2 forwarding tables
    'arp',           -- L3 neighbor cache
    'route',         -- routing table
    'traceroute',
    'inferred'       -- derived by correlation, weakest
);

CREATE TYPE entity_state AS ENUM ('active', 'stale', 'retired');

CREATE TYPE dep_method AS ENUM (
    'default_route',     -- path toward gateway
    'gateway_distance',  -- hop count to egress
    'stp_root',          -- spanning-tree topology
    'traffic_asymmetry', -- flow volume direction
    'manual',
    'composite'          -- weighted blend of the above
);

-- ------------------------------------------------------------
-- TENANCY / LOCATION
-- ------------------------------------------------------------

CREATE TABLE tenant (
    tenant_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE site (
    site_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    address       TEXT,
    UNIQUE (tenant_id, name)
);

-- ------------------------------------------------------------
-- DEVICE
-- The canonical device record. Deliberately thin: identifiers
-- that vary by source live in device_identity, not here.
-- ------------------------------------------------------------

CREATE TABLE device (
    device_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    site_id       UUID REFERENCES site(site_id) ON DELETE SET NULL,

    display_name  TEXT NOT NULL,          -- best-guess canonical name
    vendor        TEXT,                   -- normalized: cisco, meraki, dell...
    model         TEXT,
    os_version    TEXT,
    role          TEXT,                   -- core, distribution, access, firewall, host
    mgmt_ip       INET,

    state         entity_state NOT NULL DEFAULT 'active',
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON device (tenant_id, state);
CREATE INDEX ON device (tenant_id, mgmt_ip);

-- ------------------------------------------------------------
-- DEVICE IDENTITY  —  the entity-resolution core
--
-- One physical device is seen as different strings by different
-- sources: sysName from SNMP, chassis-id from LLDP, serial from a
-- vendor API, a MAC from an ARP table. Each observation lands here
-- and is CLAIMED by a device. Merging/splitting devices means
-- repointing rows in this table, not rewriting device rows.
-- ------------------------------------------------------------

CREATE TABLE device_identity (
    identity_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    device_id     UUID REFERENCES device(device_id) ON DELETE SET NULL,

    id_type       TEXT NOT NULL,   -- sysname | chassis_id | serial | mgmt_ip | base_mac | vendor_uid
    id_value      TEXT NOT NULL,
    source        source_kind NOT NULL,

    confidence    REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, id_type, id_value, source)
);

CREATE INDEX ON device_identity (tenant_id, id_type, id_value);
CREATE INDEX ON device_identity (device_id);

-- ------------------------------------------------------------
-- INTERFACE
-- ------------------------------------------------------------

CREATE TABLE interface (
    interface_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    device_id     UUID NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,

    if_index      INTEGER,                -- IF-MIB ifIndex. NOT stable across reboots.
    if_name       TEXT NOT NULL,          -- ifName / ifDescr, normalized
    if_alias      TEXT,                   -- operator description field
    mac_address   MACADDR,
    speed_bps     BIGINT,
    if_type       TEXT,                   -- ethernet, lag, svi, tunnel, wan

    admin_status  TEXT,
    oper_status   TEXT,

    state         entity_state NOT NULL DEFAULT 'active',
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (device_id, if_name)
);

CREATE INDEX ON interface (tenant_id, device_id);
CREATE INDEX ON interface (mac_address);

-- ------------------------------------------------------------
-- LINK  —  UNDIRECTED physical adjacency
--
-- Canonical ordering (endpoint_a < endpoint_b) enforced so the
-- same physical link cannot be inserted twice under two directions.
-- ------------------------------------------------------------

CREATE TABLE link (
    link_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,

    endpoint_a    UUID NOT NULL REFERENCES interface(interface_id) ON DELETE CASCADE,
    endpoint_b    UUID NOT NULL REFERENCES interface(interface_id) ON DELETE CASCADE,

    -- Aggregate belief this link exists, computed from link_evidence.
    confidence    REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    -- Set true when an operator asserts or denies the link. Locks it.
    pinned        BOOLEAN NOT NULL DEFAULT FALSE,

    state         entity_state NOT NULL DEFAULT 'active',
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (endpoint_a < endpoint_b),
    UNIQUE (endpoint_a, endpoint_b)
);

CREATE INDEX ON link (tenant_id, state);
CREATE INDEX ON link (endpoint_a);
CREATE INDEX ON link (endpoint_b);

-- Multiple sources may attest the same link. Keep them all —
-- agreement across independent sources IS the confidence signal.
CREATE TABLE link_evidence (
    evidence_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    link_id       UUID NOT NULL REFERENCES link(link_id) ON DELETE CASCADE,

    source        source_kind NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Raw claim as the source stated it, for audit and debugging.
    raw_claim     JSONB,
    -- FALSE = this source actively contradicts the link.
    asserts       BOOLEAN NOT NULL DEFAULT TRUE,

    UNIQUE (link_id, source, observed_at)
);

CREATE INDEX ON link_evidence (link_id, source);

-- ------------------------------------------------------------
-- DEPENDENCY  —  DERIVED directed layer
--
-- Separate from link on purpose. Physical adjacency is observed;
-- "downstream of" is computed and may be wrong. Suppression logic
-- reads this table and MUST respect confidence.
-- ------------------------------------------------------------

CREATE TABLE dependency (
    dependency_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,

    upstream_id   UUID NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
    downstream_id UUID NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
    via_link_id   UUID REFERENCES link(link_id) ON DELETE SET NULL,

    method        dep_method NOT NULL,
    confidence    REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    -- Below this, alert suppression must NOT fire. Wrong suppression
    -- hides real outages; that is worse than alert spam.
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (upstream_id <> downstream_id),
    UNIQUE (upstream_id, downstream_id, method)
);

CREATE INDEX ON dependency (tenant_id, downstream_id);
CREATE INDEX ON dependency (tenant_id, upstream_id);

-- Suppression-safe view. Tune the threshold with the eval harness;
-- do not guess it.
CREATE VIEW dependency_trusted AS
SELECT * FROM dependency WHERE confidence >= 0.80;

-- ------------------------------------------------------------
-- METRICS  —  normalized, entity-attached
-- OID -> metric_name mapping is ADOPTED from snmp_exporter /
-- Kentik profiles. Do not hand-author it.
-- ------------------------------------------------------------

CREATE TABLE metric_sample (
    tenant_id     UUID NOT NULL,
    entity_type   TEXT NOT NULL,          -- 'device' | 'interface'
    entity_id     UUID NOT NULL,
    metric_name   TEXT NOT NULL,          -- if_in_octets, cpu_util_pct, mem_used_pct
    ts            TIMESTAMPTZ NOT NULL,
    value         DOUBLE PRECISION NOT NULL,
    unit          TEXT,
    source        source_kind NOT NULL DEFAULT 'inferred'
);

-- SELECT create_hypertable('metric_sample','ts');
CREATE INDEX ON metric_sample (tenant_id, entity_id, metric_name, ts DESC);

-- ------------------------------------------------------------
-- DISCOVERY RUNS  —  makes "self-maintaining" auditable
-- Without this you cannot prove the graph corrected itself.
-- ------------------------------------------------------------

CREATE TABLE discovery_run (
    run_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id         UUID NOT NULL REFERENCES tenant(tenant_id) ON DELETE CASCADE,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    devices_seen      INTEGER,
    links_asserted    INTEGER,
    links_retracted   INTEGER,
    -- The differentiator, expressed as a number. Track it every run.
    auto_edge_pct     REAL
);
