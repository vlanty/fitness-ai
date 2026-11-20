-- Daily training load built from Garmin's own per-activity EPOC load
-- (activityTrainingLoad). Logic follows Garmin / Firstbeat documentation:
--   acute   = sum of load over the last 7 days
--   chronic = average weekly load over the last 28 days (28-day sum / 4)
--   ratio   = acute / chronic; Garmin: < 0.8 low, 0.8–1.4 optimal, above = high
--   focus   = 28-day load split by training-effect category (low/high aerobic, anaerobic)
-- These are Second Wind calculations from Garmin inputs, not Garmin's displayed values.
-- Load model: garmin_epoc_load only (v_activities.training_load); other load methods get their
-- own view rather than being summed into this one.
-- Everything is computed per user (windows PARTITION BY user_id).
WITH acts AS (
  SELECT
    user_id,
    activity_date AS d,
    training_load AS load,
    CASE
      WHEN training_effect_label IN ('RECOVERY', 'AEROBIC_BASE') THEN 'low_aerobic'
      WHEN training_effect_label IN ('TEMPO', 'LACTATE_THRESHOLD', 'VO2MAX') THEN 'high_aerobic'
      WHEN training_effect_label IN ('ANAEROBIC_CAPACITY', 'SPEED', 'SPRINT') THEN 'anaerobic'
      WHEN IFNULL(anaerobic_te, 0) >= 2 AND IFNULL(anaerobic_te, 0) >= IFNULL(aerobic_te, 0) THEN 'anaerobic'
      WHEN IFNULL(aerobic_te, 0) >= 3 THEN 'high_aerobic'
      ELSE 'low_aerobic'
    END AS focus
  FROM `${activities}`
  WHERE training_load IS NOT NULL
),
days AS (
  SELECT u.user_id, d
  FROM (SELECT user_id, MIN(d) AS first_d FROM acts GROUP BY user_id) AS u,
       UNNEST(GENERATE_DATE_ARRAY(u.first_d, CURRENT_DATE())) AS d
),
daily AS (
  SELECT
    days.user_id,
    days.d,
    IFNULL(SUM(acts.load), 0) AS load,
    IFNULL(SUM(IF(acts.focus = 'low_aerobic', acts.load, 0)), 0) AS low_aerobic,
    IFNULL(SUM(IF(acts.focus = 'high_aerobic', acts.load, 0)), 0) AS high_aerobic,
    IFNULL(SUM(IF(acts.focus = 'anaerobic', acts.load, 0)), 0) AS anaerobic,
    COUNT(acts.d) AS activities
  FROM days
  LEFT JOIN acts ON acts.user_id IS NOT DISTINCT FROM days.user_id AND acts.d = days.d
  GROUP BY days.user_id, days.d
),
vo2 AS (
  SELECT user_id, date AS d, vo2max FROM `${health}` WHERE vo2max IS NOT NULL
),
daily_vo2 AS (
  SELECT daily.*, vo2.vo2max AS vo2_today
  FROM daily LEFT JOIN vo2 ON vo2.user_id IS NOT DISTINCT FROM daily.user_id AND vo2.d = daily.d
),
win AS (
  SELECT
    user_id,
    d,
    load AS daily_load,
    activities,
    SUM(load) OVER w7 AS acute_load,
    SUM(load) OVER w28 / 4 AS chronic_load,
    SUM(low_aerobic) OVER w28 AS focus_low_aerobic_28d,
    SUM(high_aerobic) OVER w28 AS focus_high_aerobic_28d,
    SUM(anaerobic) OVER w28 AS focus_anaerobic_28d,
    COUNT(*) OVER w28 AS history_days,
    MAX(IF(activities > 0, d, NULL)) OVER (PARTITION BY user_id ORDER BY UNIX_DATE(d) ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS last_activity_date,
    -- VO2 max is only recalculated on some days: carry the latest known value forward.
    LAST_VALUE(vo2_today IGNORE NULLS) OVER (PARTITION BY user_id ORDER BY UNIX_DATE(d) RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS vo2max,
    LAST_VALUE(vo2_today IGNORE NULLS) OVER (PARTITION BY user_id ORDER BY UNIX_DATE(d) RANGE BETWEEN UNBOUNDED PRECEDING AND 28 PRECEDING) AS vo2max_28d_ago
  FROM daily_vo2
  WINDOW
    w7 AS (PARTITION BY user_id ORDER BY UNIX_DATE(d) RANGE BETWEEN 6 PRECEDING AND CURRENT ROW),
    w28 AS (PARTITION BY user_id ORDER BY UNIX_DATE(d) RANGE BETWEEN 27 PRECEDING AND CURRENT ROW)
)
SELECT
  win.user_id,
  win.d AS date,
  'garmin_epoc_load' AS load_method,
  daily_load,
  activities,
  acute_load,
  chronic_load,
  SAFE_DIVIDE(acute_load, chronic_load) AS load_ratio,
  CASE
    WHEN history_days < 28 OR chronic_load = 0 THEN NULL
    WHEN SAFE_DIVIDE(acute_load, chronic_load) < 0.8 THEN 'low'
    WHEN SAFE_DIVIDE(acute_load, chronic_load) <= 1.4 THEN 'optimal'
    ELSE 'high'
  END AS load_ratio_zone,
  focus_low_aerobic_28d,
  focus_high_aerobic_28d,
  focus_anaerobic_28d,
  last_activity_date,
  vo2max,
  vo2max_28d_ago
FROM win
