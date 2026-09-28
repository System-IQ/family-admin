# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Configuration
# ═══════════════════════════════════════════════════════════════
#  All configuration values in one place.
#  NOTHING is hardcoded elsewhere in the codebase.
#
#  Secrets policy:
#  - No secrets in this file.
#  - Secrets loaded from environment OR from a local secrets file
#    that is .gitignored.
#  - If env var missing, a warning is logged and a safe default used.
# ═══════════════════════════════════════════════════════════════

import os
import sys
import json
from typing import Dict, Any


# ───────────────────────────────────────────────────────────────
#  Environment detection
# ───────────────────────────────────────────────────────────────

IS_ANDROID = hasattr(sys, 'getandroidapilevel') or 'ANDROID_ROOT' in os.environ
IS_TERMUX = 'com.termux' in os.environ.get('PREFIX', '')


# ───────────────────────────────────────────────────────────────
#  Paths
# ───────────────────────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(_BASE_DIR, "family_admin.db")
LOG_PATH = os.path.join(_BASE_DIR, "server.log")
SECRETS_PATH = os.path.join(_BASE_DIR, "secrets.json")
FIREBASE_KEY_PATH = os.path.join(_BASE_DIR, "firebase-key.json")


# ───────────────────────────────────────────────────────────────
#  Server
# ───────────────────────────────────────────────────────────────

SERVER_HOST = "0.0.0.0"
SERVER_PORT = int(os.environ.get("FAT_SERVER_PORT", "5000"))
SERVER_THREADED = True
SERVER_DEBUG = False
SERVER_MAX_CONTENT_MB = 32


# ───────────────────────────────────────────────────────────────
#  API version
# ───────────────────────────────────────────────────────────────

API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"


# ───────────────────────────────────────────────────────────────
#  Authentication (loaded from secrets)
# ───────────────────────────────────────────────────────────────

ADMIN_KEY: str = ""
DEVICE_DEFAULT_KEY: str = ""


# ───────────────────────────────────────────────────────────────
#  Rate limiting
# ───────────────────────────────────────────────────────────────

RATE_LIMIT_ENABLED = True
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 300
RATE_LIMIT_MAX_BATCH_SIZE = 1000


# ───────────────────────────────────────────────────────────────
#  Retention
# ───────────────────────────────────────────────────────────────

RETENTION_LOCATIONS_DAYS = 30
RETENTION_BATTERY_DAYS = 7
RETENTION_ALERTS_DAYS = 30
RETENTION_AUDIT_DAYS = 90
CLEANUP_ENABLED = True
CLEANUP_INTERVAL_HOURS = 24


# ───────────────────────────────────────────────────────────────
#  Location processing
# ───────────────────────────────────────────────────────────────

LOCATION_POOR_ACCURACY_M = 500
LOCATION_GOOD_ACCURACY_M = 50

STOP_MIN_DURATION_SECONDS = 300
STOP_RADIUS_METERS = 50

EARTH_RADIUS_M = 6371000.0


# ───────────────────────────────────────────────────────────────
#  Battery thresholds
# ───────────────────────────────────────────────────────────────

BATTERY_LOW_PCT = 15
BATTERY_CRITICAL_PCT = 5


# ───────────────────────────────────────────────────────────────
#  Alerts
# ───────────────────────────────────────────────────────────────

ALERT_SPEED_HIGH_KMH = 120
ALERT_OFFLINE_MINUTES = 60
ALERT_DEVIATION_METERS = 500
ALERTS_ENABLED = True


# ───────────────────────────────────────────────────────────────
#  Logging
# ───────────────────────────────────────────────────────────────

LOG_LEVEL = os.environ.get("FAT_LOG_LEVEL", "INFO")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"


# ───────────────────────────────────────────────────────────────
#  Feature flags
# ───────────────────────────────────────────────────────────────

FEATURE_WEBSOCKET = True
FEATURE_SMS_FALLBACK = True
FEATURE_AI = True
FEATURE_EXPORT = True


# ───────────────────────────────────────────────────────────────
#  Protocol
# ───────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 1
DB_SCHEMA_VERSION = 1


# ───────────────────────────────────────────────────────────────
#  Secrets loader
# ───────────────────────────────────────────────────────────────

def _load_secrets() -> None:
    """
    Load secrets from environment first, then from secrets.json.
    Never raises. Logs a warning if missing.
    """
    global ADMIN_KEY, DEVICE_DEFAULT_KEY

    env_admin = os.environ.get("FAT_ADMIN_KEY")
    env_device = os.environ.get("FAT_DEVICE_DEFAULT_KEY")

    if env_admin:
        ADMIN_KEY = env_admin
    if env_device:
        DEVICE_DEFAULT_KEY = env_device

    if (not ADMIN_KEY or not DEVICE_DEFAULT_KEY) and os.path.exists(SECRETS_PATH):
        try:
            with open(SECRETS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            ADMIN_KEY = ADMIN_KEY or data.get("admin_key", "")
            DEVICE_DEFAULT_KEY = DEVICE_DEFAULT_KEY or data.get("device_default_key", "")
        except Exception as e:
            print(f"[config] WARN: could not read secrets.json: {e}", flush=True)

    if not ADMIN_KEY:
        print("[config] WARN: ADMIN_KEY is empty — auth will be open", flush=True)
    if not DEVICE_DEFAULT_KEY:
        print("[config] WARN: DEVICE_DEFAULT_KEY is empty — device auth will be open", flush=True)


def get_config_summary() -> Dict[str, Any]:
    """Safe-to-print configuration summary (no secrets)."""
    return {
        "is_android": IS_ANDROID,
        "is_termux": IS_TERMUX,
        "db_path": DB_PATH,
        "server_port": SERVER_PORT,
        "api_prefix": API_PREFIX,
        "protocol_version": PROTOCOL_VERSION,
        "db_schema_version": DB_SCHEMA_VERSION,
        "retention_locations_days": RETENTION_LOCATIONS_DAYS,
        "rate_limit_enabled": RATE_LIMIT_ENABLED,
        "features": {
            "websocket": FEATURE_WEBSOCKET,
            "sms_fallback": FEATURE_SMS_FALLBACK,
            "ai": FEATURE_AI,
            "export": FEATURE_EXPORT,
        },
        "secrets_loaded": {
            "admin_key": bool(ADMIN_KEY),
            "device_default_key": bool(DEVICE_DEFAULT_KEY),
        },
    }


_load_secrets()
