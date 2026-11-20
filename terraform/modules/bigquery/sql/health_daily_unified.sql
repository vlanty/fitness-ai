-- One row per user and day. Each field comes from the highest-priority source that has
-- a value for it that day (e.g. sleep from Garmin, weight from another source).
SELECT
  user_id,
  date,
  ARRAY_AGG(source ORDER BY _rank, source LIMIT 1)[OFFSET(0)] AS primary_source,
  ARRAY_AGG(DISTINCT source ORDER BY source) AS sources,
%{ for f in fields ~}
  ARRAY_AGG(${f} IGNORE NULLS ORDER BY _rank, source LIMIT 1)[SAFE_OFFSET(0)] AS ${f},
%{ endfor ~}
  MAX(ingested_at) AS ingested_at
FROM (
  SELECT *, ${priority} AS _rank
  FROM `${table}`
)
GROUP BY user_id, date
