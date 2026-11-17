# Secret containers only. Values are NOT managed by Terraform (they never land
# in tfvars or state): add them with scripts/setup.py or
#   gcloud secrets versions add <name> --data-file=-
variable "secret_names" {
  type = set(string)
}

resource "google_secret_manager_secret" "s" {
  for_each  = var.secret_names
  secret_id = each.key

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
