variable "gcp_project_id" {
  type = string
}

variable "gcp_region" {
  type    = string
  default = "europe-west3"
}

variable "data_sources" {
  description = "Где брать данные. Порядок = приоритет при склейке дублей (первый главный)."
  type        = list(string)
  default     = ["garmin"]

  validation {
    condition     = length(var.data_sources) > 0 && alltrue([for s in var.data_sources : contains(["garmin", "strava"], s)])
    error_message = "data_sources: непустой список из \"garmin\", \"strava\"."
  }
}

variable "owner_user_id" {
  description = "Internal id of the owner (solo mode): every data row and the bot state are keyed by it. Generated once by `scripts/setup.py config`; never change it on a running install."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{8,64}$", var.owner_user_id))
    error_message = "owner_user_id: 8–64 characters, letters, digits, - or _ (setup.py generates a UUID)."
  }
}

variable "tenancy" {
  description = "solo = one owner (the only mode today)."
  type        = string
  default     = "solo"

  validation {
    condition     = contains(["solo"], var.tenancy)
    error_message = "tenancy: only \"solo\" is supported for now."
  }
}

variable "retired_secrets" {
  description = "Секреты, которые больше не используются: остаются в Secret Manager с меткой status=retired, не удаляются."
  type        = list(string)
  default     = []
}

variable "sync_schedule" {
  description = "Cron (UTC) запуска сборщика."
  type        = string
  default     = "15 */4 * * *"
}

# Secret values (Telegram, Anthropic, data-source keys) are not Terraform
# variables: they live only in Secret Manager. See docs/setup.md / scripts/setup.py.

variable "bot_models" {
  description = "Optional Claude model per bot task (chat, analysis, weekly, alerts, morning). Empty = built-in defaults."
  type        = map(string)
  default     = {}
}

variable "bot_language" {
  description = "Bot language for menus and commands (ru, en). Empty = each user's Telegram language. Free-form answers always follow the question's language."
  type        = string
  default     = ""
}
