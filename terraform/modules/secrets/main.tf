```hcl
variable "secrets" {
  type      = map(string)
  sensitive = true
}

resource "google_secret_manager_secret" "s" {
  for_each  = var.secrets
  secret_id = each.key
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "v" {
  for_each    = var.secrets
  secret      = google_secret_manager_secret.s[each.key].id
  secret_data = each.value
}

output "secret_ids" {
  value = { for k, v in google_secret_manager_secret.s : k => v.secret_id }
}
```