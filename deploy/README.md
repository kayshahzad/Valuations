# Deploying Aletheia to GCP (Cloud Run, free-tier)

One image, two roles — a **Cloud Run service** (uvicorn + Streamlit in one
container) fronted by **direct IAP** (Google sign-in, no load balancer), plus a
**Cloud Run Job** for the data-refresh pipeline. Data lives in a **GCS bucket**
FUSE-mounted at `/app/valuation_data`. Scales to zero → no fixed monthly cost.

See [../Dockerfile](../Dockerfile), [entrypoint.sh](entrypoint.sh),
[../cloudbuild.yaml](../cloudbuild.yaml). This runbook is the imperative path;
[terraform/](terraform/) is the equivalent IaC.

> Prereqs: `gcloud` (latest — `gcloud components update`), `gsutil`, a billing
> account you can link, and `docker` for the optional local smoke test.

---

## 0. Variables (edit, then paste into your shell)

```bash
export PROJECT_ID="aletheia-$(date +%s)"      # must be globally unique
export BILLING_ACCOUNT="XXXXXX-XXXXXX-XXXXXX"  # gcloud billing accounts list
export REGION="us-central1"
export REPO="aletheia"
export IMAGE="app"
export SERVICE="aletheia"
export JOB="aletheia-pipeline"
export BUCKET="${PROJECT_ID}-data"
export RUN_SA="aletheia-run@${PROJECT_ID}.iam.gserviceaccount.com"
export ADMIN_EMAIL="kayshahzad@gmail.com"      # who may sign in via IAP
export ADMIN_EMAILS="kayshahzad@gmail.com"     # RBAC: comma-separated admins (everyone else who signs in = read-only User)
export IMG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${IMAGE}"
```

> **Two layers:** IAP decides *who can sign in at all* (the allow-list in §3);
> `ADMIN_EMAILS` decides who among them is an **Admin** (can run the pipeline,
> refresh theses, edit overrides) vs a read-only **User**. See
> [../docs/auth_architecture_comparison.md](../docs/auth_architecture_comparison.md).

---

## 1. Phase 0 — verify the container locally (optional but recommended)

```bash
cp .env.example .env && $EDITOR .env    # fill GOOGLE_API_KEY, FMP_API_KEY, SEC_IDENTITY
docker compose up --build               # open http://localhost:8080, load a ticker
docker compose run --rm app pipeline run AAPL   # one pipeline pass against ./valuation_data
```

If both work locally, the same image works on Cloud Run.

---

## 2. Phase 1 — project, APIs, registry, bucket, secrets

```bash
# Project + billing
gcloud projects create "$PROJECT_ID"
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
gcloud config set project "$PROJECT_ID"

# APIs
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com iap.googleapis.com storage.googleapis.com \
  cloudscheduler.googleapis.com

# Artifact Registry
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker --location="$REGION"

# Runtime service account (used by both the service and the Job)
gcloud iam service-accounts create aletheia-run --display-name="Aletheia runtime"

# Data bucket (source of truth for DuckDB + valuation_data)
gcloud storage buckets create "gs://${BUCKET}" --location="$REGION" --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${RUN_SA}" --role="roles/storage.objectUser"

# Secrets (create containers, then add values — values never touch code/state)
for S in google-api-key fmp-api-key sec-identity; do
  gcloud secrets create "$S" --replication-policy=automatic 2>/dev/null || true
done
printf '%s' 'YOUR_GEMINI_KEY'          | gcloud secrets versions add google-api-key --data-file=-
printf '%s' 'YOUR_FMP_KEY'             | gcloud secrets versions add fmp-api-key    --data-file=-
printf '%s' 'Your Name you@example.com'| gcloud secrets versions add sec-identity   --data-file=-   # real UA for SEC!

gcloud secrets add-iam-policy-binding google-api-key --member="serviceAccount:${RUN_SA}" --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding fmp-api-key    --member="serviceAccount:${RUN_SA}" --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding sec-identity   --member="serviceAccount:${RUN_SA}" --role=roles/secretmanager.secretAccessor
```

### Seed the bucket with your current data

```bash
# Excludes local backups; uploads DuckDB + macro CSVs + serving data (~1 GB).
gsutil -m rsync -r -x '(^|.*/)_backup_.*|.*/logs/.*' valuation_data "gs://${BUCKET}"
```

### Build + push the image (Cloud Build)

```bash
# Let Cloud Build deploy as the runtime SA.
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUM}@cloudbuild.gserviceaccount.com" --role=roles/run.admin
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" \
  --member="serviceAccount:${PROJECT_NUM}@cloudbuild.gserviceaccount.com" --role=roles/iam.serviceAccountUser

gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_SERVICE=$SERVICE,_JOB=$JOB,_BUCKET=$BUCKET,_SERVICE_SA=$RUN_SA
# First run: steps 3–4 (deploy/update) fail because the service/job don't exist
# yet — that's expected. Create them in Phase 2/3, then future builds redeploy.
```

---

## 3. Phase 2 — deploy the service + Google auth (IAP)

```bash
gcloud run deploy "$SERVICE" \
  --image="${IMG}:latest" \
  --region="$REGION" \
  --service-account="$RUN_SA" \
  --execution-environment=gen2 \
  --add-volume=name=data,type=cloud-storage,bucket="$BUCKET" \
  --add-volume-mount=volume=data,mount-path=/app/valuation_data \
  --set-secrets=GOOGLE_API_KEY=google-api-key:latest,FMP_API_KEY=fmp-api-key:latest,SEC_IDENTITY=sec-identity:latest \
  --set-env-vars=PORT=8080,ADMIN_EMAILS=${ADMIN_EMAILS} \
  --session-affinity \
  --min-instances=0 --max-instances=2 \
  --cpu=2 --memory=2Gi \
  --timeout=600 \
  --ingress=all \
  --no-allow-unauthenticated
```

Enable **direct IAP** and grant yourself access:

```bash
# One-time IAP service identity
gcloud beta services identity create --service=iap.googleapis.com

# Turn on IAP for the service (front-door Google sign-in, no LB)
gcloud beta run services update "$SERVICE" --region="$REGION" --iap

# Allow your Google account through
gcloud beta iap web add-iam-policy-binding \
  --resource-type=cloud-run --service="$SERVICE" --region="$REGION" \
  --member="user:${ADMIN_EMAIL}" --role=roles/iap.httpsResourceAccessor
```

> If `--iap` isn't recognized, run `gcloud components update`. Add teammates by
> repeating the last command with their `user:` email. **Access vs role:** the
> command above lets someone *sign in*; put their email in `ADMIN_EMAILS` to make
> them an Admin (otherwise they're a read-only User).

### (Recommended) Lock IAP JWT verification to the audience

The app verifies the IAP assertion's signature + issuer out of the box. To also
pin the audience, capture it once after your first sign-in and set `IAP_AUDIENCE`:

```bash
# After signing in, grab any request's assertion from Cloud Run logs, or decode
# the `aud` claim of X-Goog-IAP-JWT-Assertion (jwt.io / `python -m jwt`), then:
gcloud run services update "$SERVICE" --region="$REGION" \
  --update-env-vars=IAP_AUDIENCE='<aud-claim-value>'
```

Leaving `IAP_AUDIENCE` unset is safe (signature + issuer are still verified); the
app logs a warning noting audience wasn't checked.

Open the service URL (`gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)'`),
sign in as `$ADMIN_EMAIL`, and load a ticker. Cold start after idle is slow
(opens the 205 MB DuckDB over FUSE), then fast.

---

## 4. Phase 3 — the pipeline Job + schedule

```bash
gcloud run jobs create "$JOB" \
  --image="${IMG}:latest" \
  --region="$REGION" \
  --service-account="$RUN_SA" \
  --execution-environment=gen2 \
  --add-volume=name=data,type=cloud-storage,bucket="$BUCKET" \
  --add-volume-mount=volume=data,mount-path=/app/valuation_data \
  --set-secrets=GOOGLE_API_KEY=google-api-key:latest,FMP_API_KEY=fmp-api-key:latest,SEC_IDENTITY=sec-identity:latest \
  --args=pipeline,run,--all \
  --cpu=4 --memory=8Gi \
  --task-timeout=3600 --max-retries=1

# Run it once for a single ticker (override args):
gcloud run jobs execute "$JOB" --region="$REGION" --args=pipeline,run,AAPL --wait
```

Schedule a nightly refresh (Cloud Scheduler → Jobs run API):

```bash
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" \
  --member="serviceAccount:${RUN_SA}" --role=roles/run.invoker   # self-invoke the job
gcloud scheduler jobs create http aletheia-nightly \
  --location="$REGION" --schedule="0 6 * * *" \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run" \
  --http-method=POST \
  --oauth-service-account-email="$RUN_SA"
```

---

## 5. Verify

1. **Serving + auth:** open the URL, sign in as `$ADMIN_EMAIL`, load a ticker.
2. **Negative auth:** open in an incognito window / different Google account → IAP blocks.
3. **Pipeline:** `gcloud run jobs execute "$JOB" --region="$REGION" --args=pipeline,run,AAPL --wait`,
   then reload the ticker in the UI → updated values (bucket DuckDB changed).
4. **Schedule:** `gcloud scheduler jobs run aletheia-nightly --location="$REGION"`.
5. **Cost:** at `--min-instances=0` the service bills only per request; the Job
   bills only while running. The only variable spend is Gemini + FMP calls.

---

## 6. CI/CD — deploy from GitHub Actions (Workload Identity Federation)

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) builds the image
and rolls it out on every push to `main` — **keyless** (no SA JSON in GitHub).
Do the one-time setup below, then pushes deploy themselves.

> The workflow only swaps the image on the **existing** service/Job (preserving
> their volume/IAP/secrets/affinity), so finish §2–4 once before enabling it.

```bash
export REPO_SLUG="kayshahzad/Valuations"          # owner/repo
export DEPLOYER_SA="gh-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
export POOL="github"
export PROVIDER="github-oidc"
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# 1) Deployer service account + roles (build/push + deploy + act-as runtime SA)
gcloud iam service-accounts create gh-deployer --display-name="GitHub Actions deployer"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER_SA}" --role="$ROLE"
done
# Let the deployer deploy revisions that RUN AS the runtime SA
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" \
  --member="serviceAccount:${DEPLOYER_SA}" --role=roles/iam.serviceAccountUser

# 2) Workload Identity pool + GitHub OIDC provider (locked to your repo)
gcloud iam workload-identity-pools create "$POOL" --location=global \
  --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO_SLUG}'"

# 3) Allow the repo to impersonate the deployer SA
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO_SLUG}"

# 4) Print the values to set as GitHub repo *Variables* (Settings → Secrets and
#    variables → Actions → Variables). These are NOT secrets.
echo "GCP_PROJECT_ID   = ${PROJECT_ID}"
echo "GCP_DEPLOYER_SA  = ${DEPLOYER_SA}"
echo "GCP_WIF_PROVIDER = projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
```

Set those three as repo **Variables** (`vars.*`, referenced by the workflow),
then push to `main` — Actions builds, pushes to Artifact Registry, deploys the
service revision, and updates the pipeline Job. GHA layer caching keeps the fat
image fast on rebuilds. Watch it under the repo's **Actions** tab.

> `cloudbuild.yaml` remains usable for manual `gcloud builds submit`; the GitHub
> Actions workflow is the primary CI/CD path.

## Operational notes

- **Cold start annoying?** set `--min-instances=1` (leaves free tier) or add a
  Scheduler "warm ping" GET to the URL every 10 min during working hours.
- **Single writer:** never run two Job executions concurrently — DuckDB is a
  single-writer file. Cloud Scheduler + one nightly run respects this.
- **New code:** push to `main` → GitHub Actions (§6) builds, pushes, and rolls
  out a new revision + updates the Job. The service/Job keep their
  volume/IAP/secret config (only the image changes).
- **`gemini-3.1-pro-preview`** (config/__init__.py) is a preview id — confirm
  it's enabled for `$PROJECT_ID`, or set `LLM_MODEL` to a GA model via a secret/env.
- **Rollback:** `gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-revisions=PREV=100`.
