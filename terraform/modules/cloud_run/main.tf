```hcl
locals {
  secret_names = [
    "telegram_bot_token", "telegram_chat_id",
    "strava_client_id", "strava_client_secret", "strava_refresh_token",
    "garmin_email", "garmin_password",
    "anthropic_api_key",
  ]
  common_env_vars = [
    { name = "GCP_PROJECT", value = var.project_id },
    { name = "BQ_DATASET",  value = var.bq_dataset },
  ]
}

# ── Collector Job ──────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_job" "collector" {
  name     = "fitness-collector"
  location = var.region

  template {
    template {
      service_account = var.service_account
      max_retries     = 1

      containers {
        image = "${var.image_base}/collector:latest"

        dynamic "env" {
          for_each = local.common_env_vars
          content {
            name  = env.value.name
            value = env.value.value
          }
        }

        dynamic "env" {
          for_each = toset(local.secret_names)
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

      dynamic "env" {
        for_each = toset(local.secret_names)
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
```