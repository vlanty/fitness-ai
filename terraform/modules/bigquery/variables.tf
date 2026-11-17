variable "project_id" {
  type = string
}

variable "data_sources" {
  description = "Enabled sources in priority order (first wins on duplicates)."
  type        = list(string)
}
