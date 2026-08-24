-- 006: interface rate and utilisation, computed on read.
--
-- Rates are NOT stored. metric_sample holds raw counters; this view derives
-- rates from consecutive samples at query time. Same reasoning as freshness
-- (interface-design.md §4) and confidence: a derived number that is stored
-- can drift out of agreement with what it was derived from.
--
-- The time base is sysUpTime, not wall clock. The device's own clock is read
-- by the same SNMP walk that read the counters, so collector delay, POST
-- latency and NTP skew stay out of the denominator. Wall clock is used only
-- when a device reports no uptime.
--
-- Nothing is emitted when the interval cannot be trusted; `reason` says why.
-- A reboot is NOT a rate of zero and NOT a negative spike — it is no answer.

CREATE OR REPLACE VIEW interface_rate AS
WITH ctr AS (
    SELECT m.tenant_id,
           m.entity_id  AS interface_id,
           m.metric_name,
           m.ts,
           m.value,
           lag(m.value) OVER w AS prev_value,
           lag(m.ts)    OVER w AS prev_ts
      FROM metric_sample m
     WHERE m.entity_type = 'interface'
       AND m.metric_name IN ('if_in_octets', 'if_out_octets')
    -- `value` is a tiebreaker, not a sort preference. Two rows can share a
    -- ts: the optiplex-replay target wrote a second, frozen sample at the
    -- identical transaction timestamp until 2026-08-24 (bd1dc50). Ordering
    -- by ts alone leaves those tied, lag() picks arbitrarily, and the same
    -- pair lands in a different `reason` on each execution — a view that
    -- answers the same question differently twice cannot be alerted on.
    WINDOW w AS (PARTITION BY m.entity_id, m.metric_name ORDER BY m.ts, m.value)
),
upt AS (
    SELECT tenant_id, entity_id AS device_id, ts, value AS ticks
      FROM metric_sample
     WHERE entity_type = 'device'
       AND metric_name = 'sys_uptime_ticks'
),
j AS (
    SELECT c.tenant_id, i.device_id, c.interface_id, i.if_name, i.if_type,
           i.speed_bps, c.metric_name, c.prev_ts, c.ts,
           c.prev_value, c.value,
           u1.ticks AS ticks, u0.ticks AS prev_ticks
      FROM ctr c
      JOIN interface i ON i.interface_id = c.interface_id
      LEFT JOIN upt u1 ON u1.tenant_id = c.tenant_id
                      AND u1.device_id = i.device_id AND u1.ts = c.ts
      LEFT JOIN upt u0 ON u0.tenant_id = c.tenant_id
                      AND u0.device_id = i.device_id AND u0.ts = c.prev_ts
),
d AS (
    SELECT j.*,
           j.value - j.prev_value AS delta_octets,
           CASE WHEN j.ticks IS NOT NULL AND j.prev_ticks IS NOT NULL
                THEN (j.ticks - j.prev_ticks) / 100.0 END AS uptime_delta_s,
           CASE WHEN j.prev_ts IS NOT NULL
                THEN extract(epoch FROM j.ts - j.prev_ts) END AS wall_delta_s
      FROM j
),
r AS (
    SELECT d.*,
           CASE WHEN d.uptime_delta_s > 0 THEN d.uptime_delta_s
                ELSE d.wall_delta_s END AS delta_s,
           CASE WHEN d.uptime_delta_s > 0 THEN 'uptime'
                ELSE 'wallclock' END AS time_base
      FROM d
),
g AS (
    SELECT r.*,
           CASE
             WHEN r.prev_value IS NULL                      THEN 'first_sample'
             -- Strictly less than zero. Zero is a stalled agent clock, not a
             -- reboot: the simulated fleet is served from static .snmprec
             -- files and reports one sysUpTime forever. Treating that as a
             -- reset discards every simulated rate. Zero falls through to the
             -- wall-clock base, and time_base then reads 'wallclock' while
             -- uptime exists — which is itself the signal that the device's
             -- own clock has stopped advancing.
             WHEN r.uptime_delta_s < 0                      THEN 'uptime_reset'
             WHEN r.delta_octets < 0                        THEN 'counter_decrease'
             WHEN r.delta_s IS NULL OR r.delta_s <= 0       THEN 'no_time_base'
             ELSE 'ok'
           END AS reason
      FROM r
)
SELECT tenant_id, device_id, interface_id, if_name, metric_name,
       prev_ts, ts, delta_octets, delta_s, time_base, reason, speed_bps,
       CASE WHEN reason = 'ok'
            THEN delta_octets * 8.0 / delta_s END AS rate_bps,
       CASE WHEN reason = 'ok'
             AND speed_bps > 0
             AND lower(coalesce(if_type, '')) NOT LIKE '%loopback%'
            THEN (delta_octets * 8.0 / delta_s) / speed_bps * 100.0
       END AS util_pct
  FROM g;

COMMENT ON VIEW interface_rate IS
'Read-path rates from raw counters. Time base is sysUpTime where available. '
'rate_bps is emitted for any interface; util_pct only where a physical '
'denominator exists — a bridge has no link speed and loopback reports a '
'fictional one, and neither may be turned into a percentage.';
