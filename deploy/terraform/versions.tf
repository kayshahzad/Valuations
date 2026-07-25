terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source = "hashicorp/google"
      # GCS volumes on Cloud Run v2 and `iap_enabled` need a recent provider.
      version = ">= 6.10"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
