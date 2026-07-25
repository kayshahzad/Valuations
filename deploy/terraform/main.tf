locals {
  image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repository}/${var.image_name}"

  # Secret Manager secret ids → container env var names.
  secret_envs = {
    GOOGLE_API_KEY = google_secret_manager_secret.google_api_key.secret_id
    FMP_API_KEY    = google_secret_manager_secret.fmp_api_key.secret_id
    SEC_IDENTITY   = google_secret_manager_secret.sec_identity.secret_id
  }
}

# ── APIs ─────────────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "iap.googleapis.com",
    "storage.googleapis.com",
    "cloudscheduler.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ── Runtime service account (service + job) ──────────────────────────
resource "google_service_account" "run" {
  account_id   = "aletheia-run"
  display_name = "Aletheia runtime"
}

# ── Artifact Registry ────────────────────────────────────────────────
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = var.repository
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# ── Data bucket (source of truth for DuckDB + valuation_data) ────────
resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-data"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  depends_on                  = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "run_bucket" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.run.email}"
}

# ── Secrets (containers only; add versions with `gcloud secrets versions add`) ──
resource "google_secret_manager_secret" "google_api_key" {
  secret_id = "google-api-key"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret" "fmp_api_key" {
  secret_id = "fmp-api-key"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret" "sec_identity" {
  secret_id = "sec-identity"
  replication { auto {} }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "run_secrets" {
  for_each  = local.secret_envs
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run.email}"
}

# ── Cloud Run service (uvicorn + Streamlit, GCS volume, IAP) ─────────
resource "google_cloud_run_v2_service" "app" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  # Direct IAP (no load balancer). Requires provider >= 6.10; if your provider
  # is older, drop this line and enable IAP via the gcloud step in the runbook.
  iap_enabled = true

  template {
    service_account       = google_service_account.run.email
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2" # required for GCS volumes
    session_affinity      = true                          # Streamlit is stateful

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = 2
    }

    volumes {
      name = "data"
      gcs {
        bucket    = google_storage_bucket.data.name
        read_only = false
      }
    }

    containers {
      image = "${local.image}:latest"
      ports { container_port = 8080 }
      resources {
        limits = { cpu = "2", memory = "2Gi" }
      }
      volume_mounts {
        name       = "data"
        mount_path = "/app/valuation_data"
      }
      env {
        name  = "PORT"
        value = "8080"
      }
      # RBAC: comma-separated Admin emails (everyone else who signs in = User).
      env {
        name  = "ADMIN_EMAILS"
        value = join(",", var.admin_emails)
      }
      # Optional: pin IAP JWT audience for full verification (blank = off).
      env {
        name  = "IAP_AUDIENCE"
        value = var.iap_audience
      }
      dynamic "env" {
        for_each = local.secret_envs
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }

  # New revisions come from Cloud Build (`gcloud run deploy` / cloudbuild.yaml);
  # don't let `terraform apply` revert the image to :latest.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_project_service.apis, google_secret_manager_secret_iam_member.run_secrets]
}

# ── Cloud Run Job (pipeline) ─────────────────────────────────────────
resource "google_cloud_run_v2_job" "pipeline" {
  name     = var.job_name
  location = var.region

  template {
    template {
      service_account       = google_service_account.run.email
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      max_retries           = 1
      timeout               = "3600s"

      volumes {
        name = "data"
        gcs {
          bucket    = google_storage_bucket.data.name
          read_only = false
        }
      }

      containers {
        image = "${local.image}:latest"
        args  = ["pipeline", "run", "--all"]
        resources {
          limits = { cpu = "4", memory = "8Gi" }
        }
        volume_mounts {
          name       = "data"
          mount_path = "/app/valuation_data"
        }
        dynamic "env" {
          for_each = local.secret_envs
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  depends_on = [google_project_service.apis, google_secret_manager_secret_iam_member.run_secrets]
}

# ── Nightly schedule → Jobs run API ──────────────────────────────────
resource "google_service_account_iam_member" "run_self_invoke" {
  service_account_id = google_service_account.run.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.run.email}"
}

resource "google_project_iam_member" "run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.run.email}"
}

resource "google_cloud_scheduler_job" "nightly" {
  name      = "aletheia-nightly"
  region    = var.region
  schedule  = var.pipeline_schedule
  time_zone = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${var.job_name}:run"
    oauth_token {
      service_account_email = google_service_account.run.email
    }
  }
  depends_on = [google_project_service.apis, google_cloud_run_v2_job.pipeline]
}
