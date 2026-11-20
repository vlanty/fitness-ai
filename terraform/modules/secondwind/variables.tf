variable "project_id" {
  type = string
}

variable "location" {
  description = "BigQuery location of the dataset."
  type        = string
  default     = "EU"
}

variable "data_sources" {
  description = "Enabled sources in priority order (first wins on duplicates). Computed values (source = calculated) rank after them."
  type        = list(string)
}

variable "registry_dir" {
  description = "Directory with the metric/method registries (secondwind/contracts/registry)."
  type        = string
}
