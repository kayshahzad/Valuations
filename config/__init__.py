"""Config package."""
import os
from dotenv import load_dotenv

# Load variables from .env file into os.environ
load_dotenv()

# Model Configuration
MODEL_NAME = os.environ.get("LLM_MODEL", "gemini-3.1-pro-preview")
TEMPERATURE = 0.1

# Ensure API Key is present
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("Warning: GOOGLE_API_KEY not found. Agents will run in MOCK mode.")

# SEC Configuration
SEC_IDENTITY = os.environ.get("SEC_IDENTITY", "John Doe john.doe@example.com")

# ── Auth / RBAC (Workstream 2) ──────────────────────────────────────────────
# Authentication is done at the edge by Cloud Run IAP; these knobs drive the
# in-app Admin/User authorization layer (see aletheia/auth/). The auth package
# reads the environment live — these constants are the documented canonical set.
#
# ADMIN_EMAILS          comma-separated admin emails (everyone else = User).
# IAP_AUDIENCE          Cloud Run IAP JWT audience; when set, JWT audience is
#                       verified too (recommended). Fetch it per deploy/README.
# ALETHEIA_DEV_USER     local dev only: identity to assume when no IAP header.
# ALETHEIA_AUTH_DISABLED local dev only: bypass auth entirely ("true").
ADMIN_EMAILS = {
    e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()
}
IAP_AUDIENCE = os.environ.get("IAP_AUDIENCE") or None
ALETHEIA_DEV_USER = os.environ.get("ALETHEIA_DEV_USER") or None
ALETHEIA_AUTH_DISABLED = os.environ.get("ALETHEIA_AUTH_DISABLED", "").strip().lower() in (
    "1", "true", "yes",
)
