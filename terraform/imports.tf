# One-time adoption of an EXISTING deployment whose Terraform state was lost.
# `terraform plan` reads these and maps the code to resources already in GCP;
# nothing is created or changed by the import itself.
#
# Fresh installs: delete this file (the resources do not exist yet, so the
# imports would fail). After a successful apply it can be deleted as well.

locals {
  imp_p  = var.gcp_project_id
  imp_r  = var.gcp_region
  imp_sa = "fitness-runner@${var.gcp_project_id}.iam.gserviceaccount.com"
}

import {
  for_each = toset([
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "bigquery.googleapis.com",
    "artifactregistry.googleapis.com",
  ])
  to = google_project_service.apis[each.key]
  id = "${local.imp_p}/${each.key}"
}

import {
  to = google_service_account.runner
  id = "projects/${local.imp_p}/serviceAccounts/${local.imp_sa}"
}

import {
  to = google_project_iam_member.runner_bq_editor
  id = "${local.imp_p} roles/bigquery.dataEditor serviceAccount:${local.imp_sa}"
}

import {
  to = google_project_iam_member.runner_bq_job
  id = "${local.imp_p} roles/bigquery.jobUser serviceAccount:${local.imp_sa}"
}

import {
  to = google_project_iam_member.runner_secrets
  id = "${local.imp_p} roles/secretmanager.secretAccessor serviceAccount:${local.imp_sa}"
}

import {
  to = google_project_iam_member.runner_run_invoker
  id = "${local.imp_p} roles/run.invoker serviceAccount:${local.imp_sa}"
}

import {
  to = google_artifact_registry_repository.repo
  id = "projects/${local.imp_p}/locations/${local.imp_r}/repositories/fitness-ai"
}

import {
  for_each = toset([
    "telegram_bot_token", "telegram_chat_id",
    "strava_client_id", "strava_client_secret", "strava_refresh_token",
    "garmin_email", "garmin_password",
    "anthropic_api_key",
  ])
  to = module.secrets.google_secret_manager_secret.s[each.key]
  id = "projects/${local.imp_p}/secrets/${each.key}"
}

import {
  to = module.bigquery.google_bigquery_dataset.fitness
  id = "projects/${local.imp_p}/datasets/fitness"
}

import {
  to = module.bigquery.google_bigquery_table.activities
  id = "projects/${local.imp_p}/datasets/fitness/tables/activities"
}

import {
  to = module.bigquery.google_bigquery_table.sleep
  id = "projects/${local.imp_p}/datasets/fitness/tables/sleep"
}

import {
  to = module.bigquery.google_bigquery_table.hrv
  id = "projects/${local.imp_p}/datasets/fitness/tables/hrv"
}

import {
  to = module.bigquery.google_bigquery_table.daily_stats
  id = "projects/${local.imp_p}/datasets/fitness/tables/daily_stats"
}

import {
  to = module.cloud_run.google_cloud_run_v2_job.collector
  id = "projects/${local.imp_p}/locations/${local.imp_r}/jobs/fitness-collector"
}

import {
  to = module.cloud_run.google_cloud_run_v2_service.bot
  id = "projects/${local.imp_p}/locations/${local.imp_r}/services/fitness-bot"
}

import {
  to = module.cloud_run.google_cloud_run_service_iam_member.bot_public
  id = "projects/${local.imp_p}/locations/${local.imp_r}/services/fitness-bot roles/run.invoker allUsers"
}

import {
  to = module.scheduler.google_cloud_scheduler_job.daily_sync
  id = "projects/${local.imp_p}/locations/${local.imp_r}/jobs/fitness-daily-sync"
}
