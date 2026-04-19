```hcl
output "bot_url" {
  value       = module.cloud_run.bot_url
  description = "URL Telegram бота для настройки webhook"
}

output "collector_job_name" {
  value = module.cloud_run.collector_job_name
}

output "artifact_registry" {
  value = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/fitness-ai"
}
```