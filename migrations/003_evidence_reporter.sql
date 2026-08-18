-- ============================================================
-- 003: evidence records WHO reported, not just WHEN
--
-- The original UNIQUE (link_id, source, observed_at) inserts a new row
-- every poll, so evidence grows without bound and — worse — one device
-- polled ten times looks exactly like ten devices agreeing.
--
-- Agreement is the entire point of the evidence model. Two ends of a
-- cable independently reporting the same adjacency is strong. One end
-- reporting it ten times is not stronger than reporting it once.
--
-- Keying on the reporter makes evidence idempotent: a poll refreshes
-- observed_at, and the row count IS the number of independent sources.
-- ============================================================

ALTER TABLE link_evidence
    ADD COLUMN reporter_device_id UUID REFERENCES device(device_id) ON DELETE CASCADE;

-- Which local interface the reporter saw it on. Kept for audit: it is
-- how you answer "why do you think these are connected?" later.
ALTER TABLE link_evidence
    ADD COLUMN reporter_interface_id UUID REFERENCES interface(interface_id) ON DELETE SET NULL;

ALTER TABLE link_evidence
    ADD COLUMN first_seen TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE link_evidence DROP CONSTRAINT IF EXISTS link_evidence_link_id_source_observed_at_key;

ALTER TABLE link_evidence
    ADD CONSTRAINT link_evidence_reporter_unique
    UNIQUE NULLS NOT DISTINCT (link_id, source, reporter_device_id);

CREATE INDEX ON link_evidence (link_id, observed_at DESC);
CREATE INDEX ON link_evidence (reporter_device_id);

-- Fresh evidence only. Anything not re-confirmed within the window
-- stops contributing to confidence — without this, a link retracted
-- months ago keeps its score forever and the graph never forgets.
--
-- 30 minutes assumes polls more frequent than that. Widen it if the
-- poll interval grows; a window shorter than the poll interval makes
-- every link decay to nothing between runs.
CREATE OR REPLACE VIEW link_evidence_fresh AS
SELECT * FROM link_evidence
 WHERE asserts = TRUE
   AND observed_at > now() - interval '30 minutes';
