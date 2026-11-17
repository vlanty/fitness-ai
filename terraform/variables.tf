variable "gcp_project_id" {
  type = string
}

variable "gcp_region" {
  type    = string
  default = "europe-west3"
}

variable "sync_schedule" {
  type    = string
  default = "0 1 * * *"
}

# Secret values (Telegram, Anthropic, data-source keys) are no longer Terraform
# variables: they live only in Secret Manager. See README / scripts/setup.py.
