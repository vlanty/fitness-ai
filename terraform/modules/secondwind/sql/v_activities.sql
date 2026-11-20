-- One row per real-world workout, with the column names the bot has always used.
-- Columns come from observations of the session's own source (metric registry: workout_column).
-- Duplicates across sources: the same user has a workout from another source starting within
-- ±5 min whose moving OR elapsed duration differs by at most 10%; the higher-priority source
-- is kept and `also_in` lists the others.
WITH m AS (
  SELECT
%{ for m in metrics ~}
%{ if m.text ~}
    MAX(IF(o.metric = '${m.code}', o.value_text, NULL)) AS ${m.column},
%{ else ~}
    CAST(MAX(IF(o.metric = '${m.code}', o.value_num, NULL)) AS ${m.type}) AS ${m.column},
%{ endif ~}
%{ endfor ~}
    o.session_id
  FROM `${observations}` o
  JOIN `${sessions}` s ON s.session_id = o.session_id AND s.source = o.source
  WHERE s.kind = 'workout'
    AND (o.metric != 'workout.load' OR o.method = 'garmin_epoc_load')
  GROUP BY o.session_id
),
z AS (
  SELECT session_id,
         ARRAY_AGG(STRUCT(method, bounds, anchor_lthr, anchor_hr_max, anchor_hr_rest, valid_from)
                   ORDER BY ingested_at DESC LIMIT 1)[OFFSET(0)] AS z
  FROM `${hr_zones}`
  WHERE session_id IS NOT NULL
  GROUP BY session_id
),
a AS (
  SELECT
    s.user_id, s.session_id, s.vendor_id AS activity_id, s.source, s.local_date AS activity_date,
    s.start_ts AS start_time, s.name, s.sport_vendor AS type, s.sport, s.device_id,
    m.* EXCEPT (session_id),
    z.z.method AS hr_zone_calc,
    IF(z.z IS NULL, NULL,
       ARRAY_TO_STRING(ARRAY(SELECT CAST(b AS STRING) FROM UNNEST(z.z.bounds) AS b WITH OFFSET i ORDER BY i), ',')) AS hr_zone_bounds,
    z.z.anchor_lthr AS lthr, z.z.anchor_hr_max AS fit_max_hr, z.z.anchor_hr_rest AS fit_resting_hr,
    z.z.valid_from AS zones_date,
    ${priority} AS _rank
  FROM `${sessions}` s
  LEFT JOIN m USING (session_id)
  LEFT JOIN z USING (session_id)
  WHERE s.kind = 'workout'
),
pairs AS (
  SELECT
    x.session_id AS xid, y.source AS ys,
    (y._rank < x._rank OR (y._rank = x._rank AND y.source < x.source)) AS y_wins
  FROM a AS x
  JOIN a AS y
    ON x.user_id = y.user_id
   AND x.source != y.source
   AND y.activity_date BETWEEN DATE_SUB(x.activity_date, INTERVAL 1 DAY) AND DATE_ADD(x.activity_date, INTERVAL 1 DAY)
   AND ABS(TIMESTAMP_DIFF(x.start_time, y.start_time, SECOND)) <= 300
   AND (
         ABS(IFNULL(x.duration_seconds, 0) - IFNULL(y.duration_seconds, 0))
           <= 0.1 * GREATEST(IFNULL(x.duration_seconds, 0), IFNULL(y.duration_seconds, 0))
      OR ABS(x.elapsed_seconds - y.elapsed_seconds) <= 0.1 * GREATEST(x.elapsed_seconds, y.elapsed_seconds)
   )
),
agg AS (
  SELECT xid, LOGICAL_OR(y_wins) AS beaten, ARRAY_AGG(DISTINCT ys ORDER BY ys) AS also_in
  FROM pairs
  GROUP BY xid
)
SELECT a.* EXCEPT (_rank), IFNULL(agg.also_in, []) AS also_in
FROM a
LEFT JOIN agg ON agg.xid = a.session_id
WHERE NOT IFNULL(agg.beaten, FALSE)
