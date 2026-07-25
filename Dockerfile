# ─────────────────────────────────────────────────────────────────────
# Aletheia — single image, two roles (see deploy/entrypoint.sh):
#   • Cloud Run service : uvicorn (127.0.0.1:8000) + Streamlit ($PORT)
#   • Cloud Run Job     : the data-refresh pipeline (args: pipeline run …)
# ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# curl      → health probe + entrypoint readiness check
# libgomp1  → OpenMP runtime for numpy/scipy/faiss
# build-essential → safety net for any dep without a cp312 wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      libgomp1 \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cache layer), then app code.
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# Data dir is a GCS FUSE mountpoint in prod; create it for local runs too.
RUN mkdir -p /app/valuation_data \
    && chmod +x /app/deploy/entrypoint.sh

# Run as non-root.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Local/dev signal (Cloud Run uses its own probes). Streamlit's health path.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -sf "http://127.0.0.1:${PORT}/_stcore/health" || exit 1

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
