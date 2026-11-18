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
# variables: they live only in Secret Manager. See README / scripts/setup.py.

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
