terraform {
  required_version = ">= 1.7"

  # State lives in a GCS bucket (created by scripts/setup.py):
  #   terraform init -backend-config="bucket=<project>-tfstate"
  backend "gcs" {
    prefix = "fitness-ai"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

locals {
  # Secrets every deployment needs.
  core_secrets = ["telegram_bot_token", "telegram_chat_id", "anthropic_api_key"]

  # Secrets per data source. Add a new source here (and in collector/sources).
  source_secrets = {
    garmin = ["garmin_personal_api_key"]
    strava = ["strava_client_id", "strava_client_secret", "strava_refresh_token"]
  }

  collector_secrets = distinct(flatten([for s in var.data_sources : local.source_secrets[s]]))
}

# Включаем нужные GCP APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "bigquery.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# Service account для Cloud Run
resource "google_service_account" "runner" {
  account_id   = "fitness-runner"
  display_name = "Fitness AI Runner"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "runner_bq_editor" {
  project = var.gcp_project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_bq_job" {
  project = var.gcp_project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_secrets" {
  project = var.gcp_project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_project_iam_member" "runner_run_invoker" {
  project = var.gcp_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.runner.email}"
}

# Artifact Registry для Docker образов
resource "google_artifact_registry_repository" "repo" {
  location      = var.gcp_region
  repository_id = "fitness-ai"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# Raw provider files (.FIT archives etc.)
resource "google_storage_bucket" "raw" {
  name                        = "${var.gcp_project_id}-fitness-raw"
  location                    = var.gcp_region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  depends_on                  = [google_project_service.apis]

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket_iam_member" "runner_raw" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runner.email}"
}

module "secrets" {
  source       = "./modules/secrets"
  secret_names = concat(local.core_secrets, local.collector_secrets)
  retired      = var.retired_secrets
  depends_on   = [google_project_service.apis]
}

module "bigquery" {
  source       = "./modules/bigquery"
  project_id   = var.gcp_project_id
  data_sources = var.data_sources
  depends_on   = [google_project_service.apis]
}

module "cloud_run" {
  source            = "./modules/cloud_run"
  project_id        = var.gcp_project_id
  region            = var.gcp_region
  service_account   = google_service_account.runner.email
  image_base        = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/fitness-ai"
  bq_dataset        = module.bigquery.dataset_id
  data_sources      = var.data_sources
  raw_bucket        = google_storage_bucket.raw.name
  bot_secrets       = local.core_secrets
  collector_secrets = local.collector_secrets
  depends_on        = [module.secrets, module.bigquery, google_artifact_registry_repository.repo]
}

module "scheduler" {
  source          = "./modules/scheduler"
  region          = var.gcp_region
  schedule        = var.sync_schedule
  collector_job   = module.cloud_run.collector_job_name
  service_account = google_service_account.runner.email
  project_id      = var.gcp_project_id
  depends_on      = [module.cloud_run]
}
