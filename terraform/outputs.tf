output "bot_url" {
  value       = module.cloud_run.bot_url
  description = "Bot URL, used to set the Telegram webhook"
}

output "collector_job_name" {
  value = module.cloud_run.collector_job_name
}

output "artifact_registry" {
  value = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/fitness-ai"
}

output "raw_bucket" {
  value = google_storage_bucket.raw.name
}

output "secrets_to_fill" {
  description = "Secrets that need a value (scripts/setup.py secrets)."
  value       = concat(local.core_secrets, local.collector_secrets)
}
