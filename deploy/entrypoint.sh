#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Aletheia container entrypoint — one image, two roles.
#
#   (default)              serve: uvicorn (127.0.0.1:8000) + Streamlit ($PORT)
#   pipeline <args...>     run the data-refresh pipeline (Cloud Run Job)
#
# The Cloud Run *service* runs with no args → serve branch.
# The Cloud Run *Job* sets args=[pipeline, run, --all]  → pipeline branch.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Pipeline (Job) mode ──────────────────────────────────────────────
if [[ "${1:-}" == "pipeline" ]]; then
  shift
  echo "[entrypoint] pipeline mode: python -m aletheia.cli.pipeline $*"
  exec python -m aletheia.cli.pipeline "$@"
fi

# ── Serve mode (default) ─────────────────────────────────────────────
# Cloud Run provides $PORT (defaults to 8080); Streamlit binds it.
# uvicorn stays on loopback so only the co-located Streamlit reaches it —
# API_BASE=http://localhost:8000 (the app default) works unchanged.
PORT="${PORT:-8080}"
API_PORT="${API_PORT:-8000}"

# Perf: DuckDB does many small random reads; over the GCS FUSE mount each is a
# network round-trip, making /universe (recomputes every DCF) time out. The
# serving path never writes the DB, so copy it to local disk once at startup and
# point the app at that fast copy via DUCKDB_PATH. (The pipeline Job skips this —
# it writes the canonical DB on the mount.)
MOUNTED_DB="/app/valuation_data/database/investment.duckdb"
LOCAL_DB="/tmp/investment.duckdb"
if [[ -f "$MOUNTED_DB" ]]; then
  echo "[entrypoint] copying DuckDB to local disk for fast reads…"
  if cp "$MOUNTED_DB" "$LOCAL_DB"; then
    export DUCKDB_PATH="$LOCAL_DB"
    echo "[entrypoint] DUCKDB_PATH=$LOCAL_DB ($(du -h "$LOCAL_DB" | cut -f1))"
    # Overlay persisted analyst edits (DCF overrides / acknowledgments) so they
    # survive restart/scale-to-zero (serving writes hit the ephemeral /tmp copy).
    echo "[entrypoint] restoring persisted serving-state edits…"
    python -m aletheia.serving.restore_state || echo "[entrypoint] restore skipped (non-fatal)"
  else
    echo "[entrypoint] WARN: DB copy failed; falling back to FUSE-mounted DB" >&2
  fi
fi

# st.login (Google OIDC) config — written from env/secrets only when enabled.
# Keeps the client secret out of the image; the file lives in the ephemeral
# container FS. Requires AUTH_MODE=stlogin + the AUTH_* vars (see deploy/README).
if [[ "${AUTH_MODE:-}" == "stlogin" && -n "${AUTH_CLIENT_ID:-}" ]]; then
  mkdir -p /app/.streamlit
  cat > /app/.streamlit/secrets.toml <<EOF
[auth]
redirect_uri = "${AUTH_REDIRECT_URI}"
cookie_secret = "${AUTH_COOKIE_SECRET}"
client_id = "${AUTH_CLIENT_ID}"
client_secret = "${AUTH_CLIENT_SECRET}"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
EOF
  chmod 600 /app/.streamlit/secrets.toml
  echo "[entrypoint] wrote st.login auth config (AUTH_MODE=stlogin)"
fi

echo "[entrypoint] starting uvicorn on 127.0.0.1:${API_PORT}"
uvicorn api_main:app --host 127.0.0.1 --port "${API_PORT}" &
API_PID=$!

# Best-effort wait so the first Streamlit request doesn't race a cold API.
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1 \
     || curl -sf "http://127.0.0.1:${API_PORT}/" >/dev/null 2>&1; then
    echo "[entrypoint] api is up"; break
  fi
  # Bail out early if uvicorn already died.
  if ! kill -0 "${API_PID}" 2>/dev/null; then
    echo "[entrypoint] FATAL: uvicorn exited during startup" >&2
    exit 1
  fi
  sleep 1
done

echo "[entrypoint] starting Streamlit on 0.0.0.0:${PORT}"
streamlit run streamlit_app.py \
  --server.port "${PORT}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false &
WEB_PID=$!

# If EITHER process exits, tear the container down so Cloud Run restarts a
# clean instance (never serve the UI with a dead API behind it).
wait -n "${API_PID}" "${WEB_PID}"
EXIT=$?
echo "[entrypoint] a process exited (code ${EXIT}); shutting down" >&2
kill "${API_PID}" "${WEB_PID}" 2>/dev/null || true
exit "${EXIT}"
