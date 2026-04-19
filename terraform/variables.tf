```hcl
variable "gcp_project_id" {
  type = string
}

variable "gcp_region" {
  type    = string
  default = "europe-west3"
}

variable "telegram_bot_token" {
  type      = string
  sensitive = true
}

variable "telegram_chat_id" {
  type = string
}

variable "strava_client_id" {
  type      = string
  sensitive = true
}

variable "strava_client_secret" {
  type      = string
  sensitive = true
}

variable "strava_refresh_token" {
  type      = string
  sensitive = true
}

variable "garmin_email" {
  type      = string
  sensitive = true
}

variable "garmin_password" {
  type      = string
  sensitive = true
}

variable "anthropic_api_key" {
  type      = string
  sensitive = true
}

variable "sync_schedule" {
  type    = string
  default = "0 1 * * *"
}
```