"""
OpenRouter Pulse - Configuration

API key resolution order:
  1. OPENROUTER_API_KEY environment variable
  2. api_key field in %APPDATA%/OpenRouterPulse/settings.json
  3. Empty (app shows an error banner until you set one)

To set the key for development, either export the env var or run the app
once to generate the settings file, then edit it via the tray menu
(Open Settings File...).
"""
import os
import json
from pathlib import Path


def _load_api_key() -> str:
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return env_key
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    settings_path = Path(appdata) / "OpenRouterPulse" / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            key = str(data.get("api_key", "")).strip()
            if key:
                return key
        except Exception:
            pass
    return ""


API_KEY = _load_api_key()

# -- API Endpoints --
BASE_URL = "https://openrouter.ai"
API_KEY_ENDPOINT = f"{BASE_URL}/api/v1/key"
MODELS_ENDPOINT = f"{BASE_URL}/api/v1/models"
MODELS_COUNT_ENDPOINT = f"{BASE_URL}/api/v1/models/count"  # unused in MVP
GENERATION_ENDPOINT = f"{BASE_URL}/api/v1/generation"
STATUS_URL = "https://status.openrouter.ai"

# -- Refresh Intervals (milliseconds, fallback when settings missing) --
KEY_REFRESH_INTERVAL = 60_000
STATUS_REFRESH_INTERVAL = 120_000
MODELS_REFRESH_INTERVAL = 1_800_000

# -- Alert Thresholds (fallback when settings missing) --
CREDIT_WARNING_THRESHOLD = 5.0
CREDIT_CRITICAL_THRESHOLD = 1.0
CREDIT_DANGER_PERCENT = 0.20
CREDIT_CRITICAL_PERCENT = 0.05

# -- Window Settings --
DASHBOARD_WIDTH = 420
DASHBOARD_MIN_HEIGHT = 680
DASHBOARD_MAX_HEIGHT = 900

# -- App Info --
APP_NAME = "OpenRouter Pulse"
APP_VERSION = "0.1.0"
APP_ORG = "OpenRouterPulse"

# -- Startup Registry --
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_REG_NAME = "OpenRouterPulse"

# -- Links --
OPENROUTER_DASHBOARD_URL = "https://openrouter.ai/activity"
OPENROUTER_CREDITS_URL = "https://openrouter.ai/credits"
OPENROUTER_SETTINGS_URL = "https://openrouter.ai/settings/keys"
OPENROUTER_MODELS_URL = "https://openrouter.ai/models"
