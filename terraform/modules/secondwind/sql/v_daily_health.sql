-- One row per user and day, with the column names the bot has always used.
-- Generated from the metric registry: every metric with a daily_column is a column.
-- A metric group (hrv, sleep, rhr, ...) comes from ONE source per day: the highest-priority
-- source that has any metric of the group that day. Values are never mixed within a group.
-- For values that change during the day, the latest revision (as_of_ts, then ingested_at) wins.
WITH o AS (
  SELECT
    user_id, local_date, source, metric, method, value_num, value_text,
    CASE metric
%{ for m in metrics ~}
      WHEN '${m.code}' THEN '${m.group}'
%{ endfor ~}
    END AS grp,
    ${priority} AS _rank,
    ROW_NUMBER() OVER (PARTITION BY user_id, local_date, source, metric
                       ORDER BY as_of_ts DESC, ingested_at DESC) AS _rn
  FROM `${observations}`
  WHERE session_id IS NULL
    AND metric IN (${join(", ", [for m in metrics : "'${m.code}'"])})
),
best AS (
  SELECT user_id, local_date, grp, ARRAY_AGG(source ORDER BY _rank, source LIMIT 1)[OFFSET(0)] AS source
  FROM o
  WHERE _rn = 1
  GROUP BY user_id, local_date, grp
),
pick AS (
  SELECT o.* FROM o JOIN best USING (user_id, local_date, grp, source) WHERE o._rn = 1
),
sleep AS (
  SELECT user_id, local_date,
         ARRAY_AGG(STRUCT(
           DATETIME(TIMESTAMP_ADD(start_ts, INTERVAL IFNULL(tz_offset_min, 0) MINUTE)) AS sleep_start,
           DATETIME(TIMESTAMP_ADD(end_ts, INTERVAL IFNULL(tz_offset_min, 0) MINUTE)) AS sleep_end)
           ORDER BY ${priority}, IFNULL(is_main, FALSE) DESC, end_ts DESC LIMIT 1)[OFFSET(0)] AS s
  FROM `${sessions}`
  WHERE kind = 'sleep'
  GROUP BY user_id, local_date
),
days AS (
  SELECT user_id, local_date,
%{ for m in metrics ~}
%{ if m.text ~}
    MAX(IF(metric = '${m.code}', value_text, NULL)) AS ${m.column},
%{ else ~}
    CAST(MAX(IF(metric = '${m.code}', value_num, NULL)) AS ${m.type}) AS ${m.column},
%{ endif ~}
%{ endfor ~}
%{ for g in groups ~}
    MAX(IF(grp = '${g}', source, NULL)) AS ${g}_source,
    MAX(IF(metric = '${lead[g]}', method, NULL)) AS ${g}_method,
%{ endfor ~}
    ARRAY_AGG(DISTINCT source ORDER BY source) AS sources
  FROM pick
  GROUP BY user_id, local_date
)
SELECT
  COALESCE(d.user_id, s.user_id) AS user_id,
  COALESCE(d.local_date, s.local_date) AS date,
  s.s.sleep_start,
  s.s.sleep_end,
  d.* EXCEPT (user_id, local_date)
FROM days d
FULL OUTER JOIN sleep s ON s.user_id = d.user_id AND s.local_date = d.local_date
