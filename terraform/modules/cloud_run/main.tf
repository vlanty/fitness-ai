locals {
  common_env_vars = [
    { name = "GCP_PROJECT", value = var.project_id },
    { name = "BQ_DATASET", value = var.bq_dataset },
    { name = "DATA_SOURCES", value = join(",", var.data_sources) },
  ]
  collector_env_vars = concat(local.common_env_vars, [
    { name = "RAW_BUCKET", value = var.raw_bucket },
  ])
}

# ── Collector Job ──────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_job" "collector" {
  name     = "fitness-collector"
  location = var.region

  # gcloud deploys stamp these; not part of the configuration.
  lifecycle {
    ignore_changes = [client, client_version]
  }

  template {
    template {
      service_account = var.service_account
      max_retries     = 1

      containers {
        image = "${var.image_base}/collector:latest"

        dynamic "env" {
          for_each = local.collector_env_vars
          content {
            name  = env.value.name
            value = env.value.value
          }
        }

        # Only the enabled sources' secrets.
        dynamic "env" {
          for_each = toset(var.collector_secrets)
          content {
            name = upper(env.value)
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }

        resources {
          limits = { cpu = "1", memory = "512Mi" }
        }
      }
      timeout = "7200s"
    }
  }
}

# ── Bot Service ────────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "bot" {
  name     = "fitness-bot"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  # gcloud deploys stamp these; not part of the configuration.
  lifecycle {
    ignore_changes = [client, client_version]
  }

  template {
    service_account = var.service_account

    containers {
      image = "${var.image_base}/bot:latest"

      ports { container_port = 8080 }

      dynamic "env" {
        for_each = local.common_env_vars
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      # Fixed bot language (menus, commands); empty = the user's Telegram language.
      dynamic "env" {
        for_each = var.bot_language == "" ? [] : [var.bot_language]
        content {
          name  = "BOT_LANGUAGE"
          value = env.value
        }
      }

      # Optional Claude model overrides per task, e.g. { chat = "claude-opus-5-5" } -> MODEL_CHAT.
      dynamic "env" {
        for_each = var.bot_models
        content {
          name  = "MODEL_${upper(env.key)}"
          value = env.value
        }
      }

      # The bot never talks to data sources: Telegram + Anthropic only.
      dynamic "env" {
        for_each = toset(var.bot_secrets)
        content {
          name = upper(env.value)
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
    }
  }
}

# Разрешаем Telegram слать webhook без авторизации
resource "google_cloud_run_service_iam_member" "bot_public" {
  location = var.region
  service  = google_cloud_run_v2_service.bot.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "collector_job_name" {
  value = google_cloud_run_v2_job.collector.name
}

output "bot_url" {
  value = google_cloud_run_v2_service.bot.uri
}
