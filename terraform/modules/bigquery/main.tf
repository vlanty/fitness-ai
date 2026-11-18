resource "google_bigquery_dataset" "fitness" {
  dataset_id  = "fitness"
  description = "Fitness AI data"
  location    = "EU"
}

locals {
  # Source priority for de-duplication: first in var.data_sources wins.
  # Sources no longer enabled (e.g. historical Strava rows) rank last.
  priority_sql = "CASE source ${join(" ", [for i, s in var.data_sources : "WHEN '${s}' THEN ${i}"])} ELSE 999 END"

  health_fields = [
    "sleep_start", "sleep_end", "sleep_seconds", "deep_sleep_seconds", "light_sleep_seconds",
    "rem_sleep_seconds", "awake_seconds", "sleep_score",
    "hrv_last_night", "hrv_weekly_avg", "hrv_status", "hrv_5min_high", "hrv_baseline_low", "hrv_baseline_high",
    "resting_hr", "stress_avg",
    "body_battery_high", "body_battery_low", "body_battery_charged", "body_battery_drained",
    "steps", "active_calories", "spo2_avg", "respiration_avg", "vo2max",
  ]
}

# ── Normalized tables (all sources, key: source + id/date) ────────────────────
resource "google_bigquery_table" "activities" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "activities"
  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "activity_date"
  }

  # Columns may only be appended (in-place); never reorder or drop.
  schema = jsonencode([
    { name = "activity_id", type = "STRING", mode = "REQUIRED" },
    { name = "source", type = "STRING", mode = "REQUIRED" },
    { name = "activity_date", type = "DATE", mode = "REQUIRED" },
    { name = "start_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "name", type = "STRING", mode = "NULLABLE" },
    { name = "type", type = "STRING", mode = "NULLABLE" },
    { name = "distance_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "duration_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "elevation_gain", type = "FLOAT", mode = "NULLABLE" },
    { name = "avg_heart_rate", type = "INTEGER", mode = "NULLABLE" },
    { name = "max_heart_rate", type = "INTEGER", mode = "NULLABLE" },
    { name = "avg_pace_sec_per_km", type = "FLOAT", mode = "NULLABLE" },
    { name = "calories", type = "INTEGER", mode = "NULLABLE" },
    { name = "raw_json", type = "STRING", mode = "NULLABLE" },
    { name = "aerobic_te", type = "FLOAT", mode = "NULLABLE" },
    { name = "anaerobic_te", type = "FLOAT", mode = "NULLABLE" },
    { name = "training_load", type = "FLOAT", mode = "NULLABLE" },
    { name = "vo2max", type = "FLOAT", mode = "NULLABLE" },
    { name = "raw_file", type = "STRING", mode = "NULLABLE", description = "Object path in the raw bucket (.FIT zip)" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "training_effect_label", type = "STRING", mode = "NULLABLE", description = "Garmin primary benefit, e.g. AEROBIC_BASE, TEMPO" },
    { name = "elapsed_seconds", type = "INTEGER", mode = "NULLABLE", description = "Total time incl. pauses (duration_seconds is moving time)" },
    { name = "hr_zone_calc", type = "STRING", mode = "NULLABLE", description = "How the watch computed HR zones (.FIT): percent_lthr, percent_max_hr, percent_hrr" },
    { name = "hr_zone_bounds", type = "STRING", mode = "NULLABLE", description = "Watch HR zone upper bounds z0..z5 from .FIT, comma-separated; zone N = bound[N-1]..bound[N]" },
    { name = "lthr", type = "INTEGER", mode = "NULLABLE", description = "Lactate threshold HR set on the watch at the time (.FIT)" },
    { name = "fit_max_hr", type = "INTEGER", mode = "NULLABLE", description = "Max HR set on the watch at the time (.FIT)" },
    { name = "fit_resting_hr", type = "INTEGER", mode = "NULLABLE", description = "Resting HR used by the watch at the time (.FIT)" },
  ])
}

resource "google_bigquery_table" "health_daily" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "health_daily"
  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "date"
  }
  clustering = ["source"]

  schema = jsonencode([
    { name = "source", type = "STRING", mode = "REQUIRED" },
    { name = "date", type = "DATE", mode = "REQUIRED" },
    { name = "sleep_start", type = "DATETIME", mode = "NULLABLE", description = "Local wall time" },
    { name = "sleep_end", type = "DATETIME", mode = "NULLABLE", description = "Local wall time" },
    { name = "sleep_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "deep_sleep_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "light_sleep_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "rem_sleep_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "awake_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "sleep_score", type = "INTEGER", mode = "NULLABLE" },
    { name = "hrv_last_night", type = "INTEGER", mode = "NULLABLE" },
    { name = "hrv_weekly_avg", type = "INTEGER", mode = "NULLABLE" },
    { name = "hrv_status", type = "STRING", mode = "NULLABLE" },
    { name = "resting_hr", type = "INTEGER", mode = "NULLABLE" },
    { name = "stress_avg", type = "INTEGER", mode = "NULLABLE" },
    { name = "body_battery_high", type = "INTEGER", mode = "NULLABLE" },
    { name = "body_battery_low", type = "INTEGER", mode = "NULLABLE" },
    { name = "body_battery_charged", type = "INTEGER", mode = "NULLABLE" },
    { name = "body_battery_drained", type = "INTEGER", mode = "NULLABLE" },
    { name = "steps", type = "INTEGER", mode = "NULLABLE" },
    { name = "active_calories", type = "INTEGER", mode = "NULLABLE" },
    { name = "spo2_avg", type = "FLOAT", mode = "NULLABLE" },
    { name = "respiration_avg", type = "FLOAT", mode = "NULLABLE" },
    { name = "vo2max", type = "FLOAT", mode = "NULLABLE" },
    { name = "raw_json", type = "STRING", mode = "NULLABLE" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "hrv_5min_high", type = "INTEGER", mode = "NULLABLE" },
    { name = "hrv_baseline_low", type = "INTEGER", mode = "NULLABLE", description = "Garmin personal balanced range, low" },
    { name = "hrv_baseline_high", type = "INTEGER", mode = "NULLABLE", description = "Garmin personal balanced range, high" },
  ])
}

resource "google_bigquery_table" "ingest_state" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "ingest_state"
  deletion_protection = true

  schema = jsonencode([
    { name = "source", type = "STRING", mode = "REQUIRED" },
    { name = "cursor_date", type = "DATE", mode = "NULLABLE", description = "Last fully synced day" },
    { name = "last_run_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "last_success_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "last_status", type = "STRING", mode = "NULLABLE", description = "ok | partial | failed" },
    { name = "last_error", type = "STRING", mode = "NULLABLE" },
  ])
}

# ── Views the bot reads: one row per real-world activity / day ────────────────
resource "google_bigquery_table" "activities_unified" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "activities_unified"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query = templatefile("${path.module}/sql/activities_unified.sql", {
      table    = "${var.project_id}.${google_bigquery_dataset.fitness.dataset_id}.${google_bigquery_table.activities.table_id}"
      priority = local.priority_sql
    })
  }
}

resource "google_bigquery_table" "health_daily_unified" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "health_daily_unified"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query = templatefile("${path.module}/sql/health_daily_unified.sql", {
      table    = "${var.project_id}.${google_bigquery_dataset.fitness.dataset_id}.${google_bigquery_table.health_daily.table_id}"
      priority = local.priority_sql
      fields   = local.health_fields
    })
  }
}

resource "google_bigquery_table" "training_load_daily" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "training_load_daily"
  deletion_protection = false

  view {
    use_legacy_sql = false
    query = templatefile("${path.module}/sql/training_load_daily.sql", {
      activities = "${var.project_id}.${google_bigquery_dataset.fitness.dataset_id}.${google_bigquery_table.activities_unified.table_id}"
      health     = "${var.project_id}.${google_bigquery_dataset.fitness.dataset_id}.${google_bigquery_table.health_daily_unified.table_id}"
    })
  }
}

# ── Legacy tables (written by the removed password-based Garmin collector) ────
# Kept untouched; nothing reads or writes them any more.
resource "google_bigquery_table" "sleep" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "sleep"
  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "sleep_date"
  }

  schema = jsonencode([
    { name = "sleep_date", type = "DATE", mode = "REQUIRED" },
    { name = "start_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "end_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "duration_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "deep_sleep_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "light_sleep_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "rem_sleep_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "awake_seconds", type = "INTEGER", mode = "NULLABLE" },
    { name = "sleep_score", type = "INTEGER", mode = "NULLABLE" },
    { name = "avg_spo2", type = "FLOAT", mode = "NULLABLE" },
    { name = "avg_respiration", type = "FLOAT", mode = "NULLABLE" },
    { name = "raw_json", type = "STRING", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "hrv" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "hrv"
  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "measurement_date"
  }

  schema = jsonencode([
    { name = "measurement_date", type = "DATE", mode = "REQUIRED" },
    { name = "weekly_avg", type = "INTEGER", mode = "NULLABLE" },
    { name = "last_night_avg", type = "INTEGER", mode = "NULLABLE" },
    { name = "last_night_5min_high", type = "INTEGER", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "NULLABLE" },
    { name = "raw_json", type = "STRING", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "daily_stats" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "daily_stats"
  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "stat_date"
  }

  schema = jsonencode([
    { name = "stat_date", type = "DATE", mode = "REQUIRED" },
    { name = "resting_heart_rate", type = "INTEGER", mode = "NULLABLE" },
    { name = "total_steps", type = "INTEGER", mode = "NULLABLE" },
    { name = "total_distance_meters", type = "FLOAT", mode = "NULLABLE" },
    { name = "active_calories", type = "INTEGER", mode = "NULLABLE" },
    { name = "bmr_calories", type = "INTEGER", mode = "NULLABLE" },
    { name = "stress_avg", type = "INTEGER", mode = "NULLABLE" },
    { name = "body_battery_high", type = "INTEGER", mode = "NULLABLE" },
    { name = "body_battery_low", type = "INTEGER", mode = "NULLABLE" },
    { name = "raw_json", type = "STRING", mode = "NULLABLE" },
  ])
}

output "dataset_id" {
  value = google_bigquery_dataset.fitness.dataset_id
}
