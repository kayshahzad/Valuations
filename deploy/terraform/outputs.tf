output "service_url" {
  value       = google_cloud_run_v2_service.app.uri
  description = "IAP-gated URL of the Aletheia UI."
}

output "runtime_service_account" {
  value       = google_service_account.run.email
  description = "SA used by the service and the pipeline Job."
}

output "data_bucket" {
  value       = google_storage_bucket.data.name
  description = "Seed this with `gsutil -m rsync -r valuation_data gs://<bucket>`."
}

output "image_repo" {
  value       = local.image
  description = "Push images here (Cloud Build handles this)."
}

output "next_steps" {
  value = <<-EOT
    1) Add secret values:
         printf %s 'KEY'  | gcloud secrets versions add google-api-key --data-file=-
         printf %s 'KEY'  | gcloud secrets versions add fmp-api-key    --data-file=-
         printf %s 'Name you@example.com' | gcloud secrets versions add sec-identity --data-file=-
    2) Seed data:   gsutil -m rsync -r -x '(^|.*/)_backup_.*' valuation_data gs://${google_storage_bucket.data.name}
    3) Build image: gcloud builds submit --config ../../cloudbuild.yaml
    4) Grant IAP access (provider-version-safe path):
         gcloud beta iap web add-iam-policy-binding --resource-type=cloud-run \
           --service=${var.service_name} --region=${var.region} \
           --member=user:kayshahzad@gmail.com --role=roles/iap.httpsResourceAccessor
  EOT
}
