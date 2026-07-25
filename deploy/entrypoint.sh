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
