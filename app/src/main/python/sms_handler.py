# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — SMS Fallback Handler
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Provide SMS fallback when internet is unavailable.
#
#  Design:
#    - Two directions:
#        1. Server → Client device (send location request / command)
#        2. Client device → Server (deliver location when offline)
#    - Actual sending is done by Kotlin (Android's SmsManager).
#      Python only:
#          - Validates phone numbers
#          - Builds a compact, signed text payload
#          - Parses incoming text payloads
#    - Payload format is line-based, easy to parse on both sides.
#
#  Payload format (single SMS):
#      FAT1|<event_id>|<lat>|<lon>|<accuracy>|<timestamp_ms>
#    Example:
#      FAT1|a3f8...|36.155|44.074|8.5|1700000000000
#
#  Length check:
#    GSM-7 SMS = 160 chars (single), 153 (multipart)
#    Our payload is ~70-90 chars → fits in 1 SMS.
#
#  Note:
#    No cryptography yet — signing is a TODO for phase 15.
#    For now we only validate format and length.
# ═══════════════════════════════════════════════════════════════

import re
import time
import uuid
from typing import Optional, Dict, Any, Tuple


# ───────────────────────────────────────────────────────────────
#  Constants
# ───────────────────────────────────────────────────────────────

SMS_PREFIX = "FAT1"
SMS_MAX_LEN = 160
SMS_MULTIPART_SAFE_LEN = 150   # leave room for headers


# ───────────────────────────────────────────────────────────────
#  Phone number validation
# ───────────────────────────────────────────────────────────────

# We accept E.164-ish numbers:
#   optional +, 7-15 digits, no spaces after normalization.
_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


def normalize_phone(raw: Any) -> Optional[str]:
    """
    Return a normalized phone string (digits + optional leading +) or None.
    Strips spaces, dashes, parentheses.
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    # Remove common separators
    s = re.sub(r"[\s\-()]+", "", s)
    if not s:
        return None
    if not _PHONE_RE.match(s):
        return None
    return s


def validate_phone(raw: Any) -> Tuple[bool, Optional[str]]:
    """Return (ok, error_message)."""
    if not isinstance(raw, str):
        return False, "phone must be a string"
    if len(raw) > 32:
        return False, "phone too long"
    norm = normalize_phone(raw)
    if norm is None:
        return False, "phone format invalid"
    return True, None


# ───────────────────────────────────────────────────────────────
#  Build outgoing SMS
# ───────────────────────────────────────────────────────────────

def build_location_sms(lat: float, lon: float,
                       accuracy: Optional[float] = None,
                       timestamp_ms: Optional[int] = None,
                       event_id: Optional[str] = None) -> str:
    """
    Build a compact location SMS body.
    Returns a string that fits in one SMS under normal conditions.
    """
    if event_id is None:
        event_id = str(uuid.uuid4())
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if accuracy is None:
        acc_str = ""
    else:
        acc_str = f"{float(accuracy):.1f}"

    body = "|".join([
        SMS_PREFIX,
        event_id[:36],
        f"{lat:.6f}",
        f"{lon:.6f}",
        acc_str,
        str(int(timestamp_ms)),
    ])

    if len(body) > SMS_MULTIPART_SAFE_LEN:
        # Trim event_id if needed (keep prefix + critical data)
        overflow = len(body) - SMS_MULTIPART_SAFE_LEN
        event_id = event_id[: max(8, 36 - overflow)]
        body = "|".join([
            SMS_PREFIX,
            event_id,
            f"{lat:.6f}",
            f"{lon:.6f}",
            acc_str,
            str(int(timestamp_ms)),
        ])
    return body


def build_simple_command_sms(command: str,
                             timestamp_ms: Optional[int] = None) -> str:
    """
    Build a tiny SMS with a plain command (e.g. "SEND_NOW").
    Format: FAT1|CMD|<command>|<timestamp_ms>
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    cmd = re.sub(r"[^A-Z0-9_]", "", command.upper())[:32]
    return f"{SMS_PREFIX}|CMD|{cmd}|{int(timestamp_ms)}"


# ───────────────────────────────────────────────────────────────
#  Parse incoming SMS
# ───────────────────────────────────────────────────────────────

def is_fat_sms(body: Any) -> bool:
    """Cheap check: does this look like our payload?"""
    return isinstance(body, str) and body.startswith(SMS_PREFIX + "|")


def parse_incoming_sms(body: Any) -> Optional[Dict[str, Any]]:
    """
    Parse a FAT1 SMS.
    Returns a dict or None if the format is not recognized.

    Possible result kinds:
        {"kind": "location", "event_id":..., "lat":..., "lon":..., ...}
        {"kind": "command",  "command":..., "timestamp":...}
    """
    if not isinstance(body, str):
        return None
    if not is_fat_sms(body):
        return None

    parts = body.split("|")
    if len(parts) < 2:
        return None

    tag = parts[1]

    # ─── Command ───
    if tag == "CMD":
        if len(parts) < 3:
            return None
        cmd = parts[2]
        ts = None
        if len(parts) >= 4:
            try:
                ts = int(parts[3])
            except ValueError:
                ts = None
        return {"kind": "command", "command": cmd, "timestamp": ts}

    # ─── Location ───
    # Format: FAT1|<event_id>|<lat>|<lon>|<acc>|<ts>
    if len(parts) < 6:
        return None

    event_id = parts[1]
    try:
        lat = float(parts[2])
        lon = float(parts[3])
        ts = int(parts[5])
    except ValueError:
        return None

    acc: Optional[float] = None
    if parts[4]:
        try:
            acc = float(parts[4])
        except ValueError:
            acc = None

    # Range check
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    return {
        "kind": "location",
        "event_id": event_id,
        "lat": lat,
        "lon": lon,
        "accuracy": acc,
        "timestamp": ts,
        "source": "sms",
    }


# ───────────────────────────────────────────────────────────────
#  Kotlin-facing convenience wrappers
# ─────────────────────────────────────────────────────────────

def prepare_outgoing_location(device_id: str,
                              lat: float, lon: float,
                              accuracy: Optional[float] = None) -> Dict[str, Any]:
    """
    Returns a dict the Kotlin side can consume:
        { "body": "...", "length": N, "device_id": "..." }
    Kotlin picks the destination phone number.
    """
    body = build_location_sms(lat=lat, lon=lon, accuracy=accuracy)
    return {
        "body": body,
        "length": len(body),
        "device_id": device_id,
    }


def handle_incoming_text(body: str) -> Optional[Dict[str, Any]]:
    """
    Entry point when Kotlin passes a raw incoming SMS body.
    Returns a parsed payload (see parse_incoming_sms) or None.
    The caller is responsible for persisting it.
    """
    return parse_incoming_sms(body)


# ───────────────────────────────────────────────────────────────
#  Diagnostics
# ───────────────────────────────────────────────────────────────

def max_payload_length() -> int:
    return SMS_MULTIPART_SAFE_LEN
