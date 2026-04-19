resource "google_bigquery_dataset" "fitness" {
  dataset_id  = "fitness"
  description = "Fitness AI data"
  location    = "EU"
}

resource "google_bigquery_table" "activities" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "activities"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "activity_date"
  }

  schema = jsonencode([
    { name = "activity_id",        type = "STRING",    mode = "REQUIRED" },
    { name = "source",             type = "STRING",    mode = "REQUIRED" },
    { name = "activity_date",      type = "DATE",      mode = "REQUIRED" },
    { name = "start_time",         type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "name",               type = "STRING",    mode = "NULLABLE" },
    { name = "type",               type = "STRING",    mode = "NULLABLE" },
    { name = "distance_meters",    type = "FLOAT64",   mode = "NULLABLE" },
    { name = "duration_seconds",   type = "INT64",     mode = "NULLABLE" },
    { name = "elevation_gain",     type = "FLOAT64",   mode = "NULLABLE" },
    { name = "avg_heart_rate",     type = "INT64",     mode = "NULLABLE" },
    { name = "max_heart_rate",     type = "INT64",     mode = "NULLABLE" },
    { name = "avg_pace_sec_per_km",type = "FLOAT64",   mode = "NULLABLE" },
    { name = "calories",           type = "INT64",     mode = "NULLABLE" },
    { name = "raw_json",           type = "STRING",    mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "sleep" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "sleep"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "sleep_date"
  }

  schema = jsonencode([
    { name = "sleep_date",         type = "DATE",      mode = "REQUIRED" },
    { name = "start_time",         type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "end_time",           type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "duration_seconds",   type = "INT64",     mode = "NULLABLE" },
    { name = "deep_sleep_seconds", type = "INT64",     mode = "NULLABLE" },
    { name = "light_sleep_seconds",type = "INT64",     mode = "NULLABLE" },
    { name = "rem_sleep_seconds",  type = "INT64",     mode = "NULLABLE" },
    { name = "awake_seconds",      type = "INT64",     mode = "NULLABLE" },
    { name = "sleep_score",        type = "INT64",     mode = "NULLABLE" },
    { name = "avg_spo2",           type = "FLOAT64",   mode = "NULLABLE" },
    { name = "avg_respiration",    type = "FLOAT64",   mode = "NULLABLE" },
    { name = "raw_json",           type = "STRING",    mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "hrv" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "hrv"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "measurement_date"
  }

  schema = jsonencode([
    { name = "measurement_date",      type = "DATE",   mode = "REQUIRED" },
    { name = "weekly_avg",            type = "INT64",  mode = "NULLABLE" },
    { name = "last_night_avg",        type = "INT64",  mode = "NULLABLE" },
    { name = "last_night_5min_high",  type = "INT64",  mode = "NULLABLE" },
    { name = "status",                type = "STRING", mode = "NULLABLE" },
    { name = "raw_json",              type = "STRING", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "daily_stats" {
  dataset_id          = google_bigquery_dataset.fitness.dataset_id
  table_id            = "daily_stats"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "stat_date"
  }

  schema = jsonencode([
    { name = "stat_date",              type = "DATE",   mode = "REQUIRED" },
    { name = "resting_heart_rate",     type = "INT64",  mode = "NULLABLE" },
    { name = "total_steps",            type = "INT64",  mode = "NULLABLE" },
    { name = "total_distance_meters",  type = "FLOAT64",mode = "NULLABLE" },
    { name = "active_calories",        type = "INT64",  mode = "NULLABLE" },
    { name = "bmr_calories",           type = "INT64",  mode = "NULLABLE" },
    { name = "stress_avg",             type = "INT64",  mode = "NULLABLE" },
    { name = "body_battery_high",      type = "INT64",  mode = "NULLABLE" },
    { name = "body_battery_low",       type = "INT64",  mode = "NULLABLE" },
    { name = "raw_json",               type = "STRING", mode = "NULLABLE" },
  ])
}

output "dataset_id" {
  value = google_bigquery_dataset.fitness.dataset_id
}