-- One row per real-world activity across all sources.
-- Duplicate = another source has an activity starting within ±5 min whose
-- duration differs by at most 10%. The higher-priority source is kept
-- (ties broken by source name); `also_in` lists where else it was found.
WITH a AS (
  SELECT *, ${priority} AS _rank
  FROM `${table}`
),
pairs AS (
  SELECT
    x.source AS xs, x.activity_id AS xid, y.source AS ys,
    (y._rank < x._rank OR (y._rank = x._rank AND y.source < x.source)) AS y_wins
  FROM a AS x
  JOIN a AS y
    ON x.source != y.source
   AND y.activity_date BETWEEN DATE_SUB(x.activity_date, INTERVAL 1 DAY) AND DATE_ADD(x.activity_date, INTERVAL 1 DAY)
   AND ABS(TIMESTAMP_DIFF(x.start_time, y.start_time, SECOND)) <= 300
   AND ABS(IFNULL(x.duration_seconds, 0) - IFNULL(y.duration_seconds, 0))
       <= 0.1 * GREATEST(IFNULL(x.duration_seconds, 0), IFNULL(y.duration_seconds, 0))
),
agg AS (
  SELECT xs, xid, LOGICAL_OR(y_wins) AS beaten, ARRAY_AGG(DISTINCT ys ORDER BY ys) AS also_in
  FROM pairs
  GROUP BY xs, xid
)
SELECT a.* EXCEPT (_rank, raw_json), IFNULL(agg.also_in, []) AS also_in
FROM a
LEFT JOIN agg ON agg.xs = a.source AND agg.xid = a.activity_id
WHERE NOT IFNULL(agg.beaten, FALSE)
