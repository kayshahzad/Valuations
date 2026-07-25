# The project + billing are created in the runbook (Phase 1) — a personal
# Gmail account without an org makes Terraform project creation fragile, so
# this stack manages resources *within* an existing project.
variable "project_id" {
  type        = string
  description = "Existing GCP project id (created via `gcloud projects create`)."
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "repository" {
  type    = string
  default = "aletheia"
}

variable "image_name" {
  type    = string
  default = "app"
}

variable "service_name" {
  type    = string
  default = "aletheia"
}

variable "job_name" {
  type    = string
  default = "aletheia-pipeline"
}

variable "iap_members" {
  type        = list(string)
  description = "Principals allowed through IAP (who may sign in), e.g. [\"user:kayshahzad@gmail.com\"]."
}

variable "admin_emails" {
  type        = list(string)
  default     = []
  description = "RBAC Admins (subset of signed-in users). Everyone else who signs in is a read-only User. e.g. [\"kayshahzad@gmail.com\"]."
}

variable "iap_audience" {
  type        = string
  default     = ""
  description = "Cloud Run IAP JWT audience. Optional; when set, the app also verifies the JWT audience. See deploy/README.md."
}

variable "pipeline_schedule" {
  type        = string
  default     = "0 6 * * *"
  description = "Cron for the nightly pipeline refresh (UTC)."
}

variable "min_instances" {
  type        = number
  default     = 0
  description = "0 = scale to zero (free tier). 1 = no cold starts (leaves free tier)."
}
