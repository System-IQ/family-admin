# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Input Validators
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Validate HTTP payloads BEFORE they hit the database.
#
#  Contract:
#    Every validator returns (ok: bool, error: Optional[str]).
#    No exceptions. No side effects. Pure functions.
#
#  Usage:
#    ok, err = validate_location_payload(data)
#    if not ok:
#        return jsonify({"error": err}), 400
# ═══════════════════════════════════════════════════════════════

from typing import Any, Dict, Optional, Tuple

from models import LocationSource


# ───────────────────────────────────────────────────────────────
#  Primitive helpers
# ───────────────────────────────────────────────────────────────

def _is_num(v: Any) -> bool:
    """True if v is int or float, but not bool."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int_like(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _as_float(v: Any) -> Optional[float]:
    if _is_num(v):
        return float(v)
    return None


def _as_int(v: Any) -> Optional[int]:
    if _is_int_like(v):
        return int(v)
    if _is_num(v) and float(v).is_integer():
        return int(v)
    return None


# ───────────────────────────────────────────────────────────────
#  Device validators
# ───────────────────────────────────────────────────────────────

def validate_device_id(v: Any) -> Tuple[bool, Optional[str]]:
    if not isinstance(v, str):
        return False, "device_id must be a string"
    if len(v) < 1 or len(v) > 128:
        return False, "device_id length must be 1..128"
    # Safe charset — alphanumerics, dash, underscore
    for ch in v:
        if not (ch.isalnum() or ch in "-_."):
            return False, "device_id contains invalid characters"
    return True, None


def validate_register_payload(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """POST /api/v1/devices/register"""
    ok, err = validate_device_id(data.get("device_id"))
    if not ok:
        return False, err

    name = data.get("name")
    if name is not None:
        if not isinstance(name, str) or len(name) > 128:
            return False, "name must be a string of length <= 128"

    model = data.get("model")
    if model is not None:
        if not isinstance(model, str) or len(model) > 128:
            return False, "model must be a string of length <= 128"

    android_version = data.get("android_version")
    if android_version is not None:
        if not isinstance(android_version, str) or len(android_version) > 64:
            return False, "android_version must be a string of length <= 64"

    return True, None


# ───────────────────────────────────────────────────────────────
#  Location validators
# ───────────────────────────────────────────────────────────────

def validate_location_payload(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """POST /api/v1/devices/{id}/locations — single point"""
    ok, err = validate_device_id(data.get("device_id"))
    if not ok:
        return False, err

    lat = _as_float(data.get("lat"))
    if lat is None:
        return False, "lat must be a number"
    if not (-90.0 <= lat <= 90.0):
        return False, "lat out of range (-90..90)"

    lon = _as_float(data.get("lon"))
    if lon is None:
        return False, "lon must be a number"
    if not (-180.0 <= lon <= 180.0):
        return False, "lon out of range (-180..180)"

    acc = data.get("accuracy")
    if acc is not None:
        acc_f = _as_float(acc)
        if acc_f is None:
            return False, "accuracy must be a number"
        if acc_f < 0 or acc_f > 1_000_000:
            return False, "accuracy out of range"

    alt = data.get("altitude")
    if alt is not None and _as_float(alt) is None:
        return False, "altitude must be a number"

    spd = data.get("speed")
    if spd is not None:
        spd_f = _as_float(spd)
        if spd_f is None:
            return False, "speed must be a number"
        if spd_f < 0 or spd_f > 1000:
            return False, "speed out of range"

    brg = data.get("bearing")
    if brg is not None:
        brg_f = _as_float(brg)
        if brg_f is None:
            return False, "bearing must be a number"
        if not (0 <= brg_f <= 360):
            return False, "bearing out of range (0..360)"

    bat = data.get("battery")
    if bat is not None:
        bat_i = _as_int(bat)
        if bat_i is None:
            return False, "battery must be an integer"
        if not (0 <= bat_i <= 100):
            return False, "battery out of range (0..100)"

    src = data.get("source")
    if src is not None:
        if not isinstance(src, str):
            return False, "source must be a string"
        if src not in LocationSource.ALL:
            return False, f"source must be one of {sorted(LocationSource.ALL)}"

    ts = data.get("timestamp")
    if ts is not None:
        ts_i = _as_int(ts)
        if ts_i is None:
            return False, "timestamp must be an integer"
        if ts_i <= 0:
            return False, "timestamp must be positive"

    return True, None


def validate_location_batch_payload(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """POST /api/v1/devices/{id}/locations/batch"""
    ok, err = validate_device_id(data.get("device_id"))
    if not ok:
        return False, err

    locations = data.get("locations")
    if not isinstance(locations, list):
        return False, "locations must be an array"
    if len(locations) == 0:
        return False, "locations array is empty"
    if len(locations) > 1000:
        return False, "locations array exceeds 1000 items per batch"

    for i, loc in enumerate(locations):
        if not isinstance(loc, dict):
            return False, f"locations[{i}] must be an object"
        ok, err = validate_location_payload(loc)
        if not ok:
            return False, f"locations[{i}]: {err}"

    return True, None


# ───────────────────────────────────────────────────────────────
#  Command validators
# ───────────────────────────────────────────────────────────────

# The full set of commands the Client understands.
ALLOWED_COMMANDS = {
    "SEND_NOW",
    "ENABLE_GPS",
    "DISABLE_GPS",
    "LOCK_SCREEN",
    "SOUND_ALERT",
    "SHOW_MESSAGE",
    "RESTART_APP",
    "SOS",
    "PING",
    "GET_STATE",
    "SET_INTERVAL",
    "SET_BATTERY_SAVER",
}


def validate_command_payload(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """POST /api/v1/devices/{id}/commands"""
    cmd = data.get("command")
    if not isinstance(cmd, str):
        return False, "command must be a string"
    if len(cmd) < 1 or len(cmd) > 64:
        return False, "command length must be 1..64"
    if cmd not in ALLOWED_COMMANDS:
        return False, f"command must be one of {sorted(ALLOWED_COMMANDS)}"

    ttl_ms = data.get("ttl_ms")
    if ttl_ms is not None:
        ttl_i = _as_int(ttl_ms)
        if ttl_i is None:
            return False, "ttl_ms must be an integer"
        if ttl_i <= 0 or ttl_i > 7 * 24 * 3600 * 1000:
            return False, "ttl_ms must be 1 ms .. 7 days"

    payload = data.get("payload")
    if payload is not None:
        if not isinstance(payload, str):
            return False, "payload must be a string"
        if len(payload) > 4096:
            return False, "payload length must be <= 4096"

    return True, None


# ───────────────────────────────────────────────────────────────
#  Geofence validators
# ───────────────────────────────────────────────────────────────

def validate_geofence_payload(data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """POST /api/v1/devices/{id}/geofences"""
    name = data.get("name")
    if not isinstance(name, str):
        return False, "name must be a string"
    if len(name) < 1 or len(name) > 64:
        return False, "name length must be 1..64"

    lat = _as_float(data.get("lat"))
    if lat is None:
        return False, "lat must be a number"
    if not (-90.0 <= lat <= 90.0):
        return False, "lat out of range"

    lon = _as_float(data.get("lon"))
    if lon is None:
        return False, "lon must be a number"
    if not (-180.0 <= lon <= 180.0):
        return False, "lon out of range"

    radius = data.get("radius", 200)
    radius_i = _as_int(radius)
    if radius_i is None:
        return False, "radius must be an integer"
    if not (10 <= radius_i <= 100_000):
        return False, "radius must be 10..100000 meters"

    return True, None


# ───────────────────────────────────────────────────────────────
#  Pagination / query helpers
# ───────────────────────────────────────────────────────────────

def parse_int_arg(
    args: Dict[str, Any],
    key: str,
    default: int,
    min_val: int,
    max_val: int,
) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Returns (ok, value, error).
    For GET query params.
    """
    raw = args.get(key)
    if raw is None:
        return True, default, None

    # Flask args are strings
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return False, None, f"{key} must be an integer"

    if v < min_val or v > max_val:
        return False, None, f"{key} must be {min_val}..{max_val}"

    return True, v, None
