variable "project_id" { type = string }
variable "region" { type = string }
variable "service_account" { type = string }
variable "image_base" { type = string }
variable "bq_dataset" { type = string }
variable "data_sources" { type = list(string) }
variable "raw_bucket" { type = string }
variable "bot_secrets" { type = list(string) }
variable "collector_secrets" { type = list(string) }
variable "bot_models" {
  type    = map(string)
  default = {}
}
