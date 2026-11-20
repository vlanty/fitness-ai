-- One row per real-world activity across all sources, per user.
-- Duplicate = the same user has an activity from another source starting within ±5 min
-- whose moving OR elapsed duration differs by at most 10% (sources count moving time
-- differently for strength, walks, swims...). The higher-priority source is kept
-- (ties broken by source name); `also_in` lists where else it was found.
-- user_id is compared with IS NOT DISTINCT FROM so rows written before user_id existed
-- (NULL) still pair up with each other.
WITH a AS (
  SELECT *, ${priority} AS _rank
  FROM `${table}`
),
pairs AS (
  SELECT
    x.user_id AS xu, x.source AS xs, x.activity_id AS xid, y.source AS ys,
    (y._rank < x._rank OR (y._rank = x._rank AND y.source < x.source)) AS y_wins
  FROM a AS x
  JOIN a AS y
    ON x.user_id IS NOT DISTINCT FROM y.user_id
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
  SELECT xu, xs, xid, LOGICAL_OR(y_wins) AS beaten, ARRAY_AGG(DISTINCT ys ORDER BY ys) AS also_in
  FROM pairs
  GROUP BY xu, xs, xid
)
SELECT a.* EXCEPT (_rank, raw_json), IFNULL(agg.also_in, []) AS also_in
FROM a
LEFT JOIN agg ON agg.xu IS NOT DISTINCT FROM a.user_id AND agg.xs = a.source AND agg.xid = a.activity_id
WHERE NOT IFNULL(agg.beaten, FALSE)
