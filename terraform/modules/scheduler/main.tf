```hcl
resource "google_cloud_scheduler_job" "daily_sync" {
  name      = "fitness-daily-sync"
  schedule  = var.schedule
  time_zone = "UTC"
  region    = var.region

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${var.collector_job}:run"

    oauth_token {
      service_account_email = var.service_account
    }
  }
}
```