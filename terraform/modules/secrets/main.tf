# Secret containers only. Values are NOT managed by Terraform (they never land
# in tfvars or state): add them with scripts/setup.py or
#   gcloud secrets versions add <name> --data-file=-
variable "secret_names" {
  description = "Secrets in use."
  type        = list(string)
}

variable "retired" {
  description = "Secrets no longer used: kept, labelled status=retired, never deleted by Terraform."
  type        = list(string)
  default     = []
}

resource "google_secret_manager_secret" "s" {
  for_each  = toset(concat(var.secret_names, var.retired))
  secret_id = each.key
  labels    = contains(var.retired, each.key) && !contains(var.secret_names, each.key) ? { status = "retired" } : {}

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "secret_ids" {
  value = { for k, v in google_secret_manager_secret.s : k => v.secret_id }
}
