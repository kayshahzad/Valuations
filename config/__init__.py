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
