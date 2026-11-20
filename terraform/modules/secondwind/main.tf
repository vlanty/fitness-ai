# Dataset `secondwind`: the universal data model (see docs: one long `observations` table with
# metadata, entities, registries; wide views for the bot generated from the metric registry).

locals {
  ds = google_bigquery_dataset.sw.dataset_id
  t  = "${var.project_id}.${google_bigquery_dataset.sw.dataset_id}"

  # Source priority: first in var.data_sources wins; our own calculations next; anything else last.
  priority_sql = "CASE source ${join(" ", [for i, s in concat(var.data_sources, ["calculated"]) : "WHEN '${s}' THEN ${i}"])} ELSE 999 END"

  metrics = yamldecode(file("${var.registry_dir}/metrics.yaml")).metrics

  # Bot views are generated from the registry: a metric with daily_column / workout_column
  # becomes a column; the group decides which source a day's value comes from.
  daily_metrics = [for m in local.metrics : {
    code = m.code, column = m.daily_column, type = m.column_type, group = m.group,
    text = m.value_type == "text"
  } if try(m.daily_column, null) != null]
  daily_groups = distinct([for m in local.daily_metrics : m.group])
  # The method shown for a group is the method of its first metric with a column.
  group_lead = { for g in local.daily_groups : g => [for m in local.daily_metrics : m.code if m.group == g][0] }

  workout_metrics = [for m in local.metrics : {
    code = m.code, column = m.workout_column, type = m.column_type, text = m.value_type == "text"
  } if try(m.workout_column, null) != null]

  tables = {
    observations     = { partition = "local_date", cluster = ["user_id", "metric", "source"], protect = true }
    sessions         = { partition = "local_date", cluster = ["user_id", "kind"], protect = true }
    laps             = { partition = null, cluster = ["user_id", "session_id"], protect = true }
    sleep_stages     = { partition = "start_ts", cluster = ["user_id"], protect = true }
    streams          = { partition = "ts", cluster = ["user_id", "session_id"], protect = true }
    devices          = { partition = null, cluster = ["user_id"], protect = true }
    hr_zones         = { partition = null, cluster = ["user_id"], protect = true }
    user_events      = { partition = null, cluster = ["user_id"], protect = true }
    baselines        = { partition = "as_of", cluster = ["user_id", "metric"], protect = true }
    ingest_state     = { partition = null, cluster = null, protect = true }
    raw_payloads     = { partition = "fetched_at", cluster = ["user_id", "source"], protect = true }
    registry_entries = { partition = null, cluster = ["kind"], protect = false }
  }
}

resource "google_bigquery_dataset" "sw" {
  dataset_id  = "secondwind"
  description = "Second Wind data model: observations with metadata, entities, registries"
  location    = var.location
}

resource "google_bigquery_table" "t" {
  for_each            = local.tables
  dataset_id          = local.ds
  table_id            = each.key
  deletion_protection = each.value.protect
  schema              = file("${path.module}/schemas/${each.key}.json")
  clustering          = each.value.cluster

  dynamic "time_partitioning" {
    for_each = each.value.partition == null ? [] : [each.value.partition]
    content {
      type  = "DAY"
      field = time_partitioning.value
    }
  }
}

# ── Views the bot reads ───────────────────────────────────────────────────────
resource "google_bigquery_table" "v_daily_health" {
  dataset_id          = local.ds
  table_id            = "v_daily_health"
  deletion_protection = false
  view {
    use_legacy_sql = false
    query = templatefile("${path.module}/sql/v_daily_health.sql", {
      observations = "${local.t}.observations"
      sessions     = "${local.t}.sessions"
      priority     = local.priority_sql
      metrics      = local.daily_metrics
      groups       = local.daily_groups
      lead         = local.group_lead
    })
  }
  depends_on = [google_bigquery_table.t]
}

resource "google_bigquery_table" "v_activities" {
  dataset_id          = local.ds
  table_id            = "v_activities"
  deletion_protection = false
  view {
    use_legacy_sql = false
    query = templatefile("${path.module}/sql/v_activities.sql", {
      observations = "${local.t}.observations"
      sessions     = "${local.t}.sessions"
      hr_zones     = "${local.t}.hr_zones"
      priority     = local.priority_sql
      metrics      = local.workout_metrics
    })
  }
  depends_on = [google_bigquery_table.t]
}

resource "google_bigquery_table" "v_training_load_daily" {
  dataset_id          = local.ds
  table_id            = "v_training_load_daily"
  deletion_protection = false
  view {
    use_legacy_sql = false
    query = templatefile("${path.module}/sql/v_training_load_daily.sql", {
      activities = "${local.t}.v_activities"
      health     = "${local.t}.v_daily_health"
    })
  }
  depends_on = [google_bigquery_table.v_activities, google_bigquery_table.v_daily_health]
}

output "dataset_id" {
  value = local.ds
}
