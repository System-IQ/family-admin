# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — FCM Sender Bridge
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Provide a clean bridge between Python and Kotlin for sending
#    Firebase Cloud Messaging (FCM) messages.
#
#  Why a bridge?
#    Firebase Admin SDK is a Java/Kotlin library with native deps
#    that are unavailable inside Chaquopy. So:
#      - Kotlin registers a handler at app startup.
#      - Python calls fcm_sender.send_to_device(...).
#      - Kotlin does the actual HTTP call to FCM v1 endpoint.
#
#  Contract:
#    Kotlin must call (from FamilyAdminApp):
#        py.getModule("fcm_sender").callAttr(
#            "register_sender", sender_callable
#        )
#    where sender_callable(payload_json: str) -> bool
#
#  Fallback behavior:
#    If no sender is registered, sends are:
#      - logged
#      - counted as "not_sent"
#      - queued via queue_manager for later retry
# ═══════════════════════════════════════════════════════════════

import json
import time
import threading
from typing import Optional, Dict, Any, Callable, List


# ───────────────────────────────────────────────────────────────
#  State
# ───────────────────────────────────────────────────────────────

_sender: Optional[Callable[[str], bool]] = None
_sender_name: Optional[str] = None
_lock = threading.Lock()

_stats = {
    "sent": 0,
    "failed": 0,
    "skipped": 0,
    "bridge_missing": 0,
}
_stats_lock = threading.Lock()


# ───────────────────────────────────────────────────────────────
#  Bridge registration (called from Kotlin)
# ───────────────────────────────────────────────────────────────

def register_sender(sender_callable: Callable[[str], bool],
                    name: str = "kotlin") -> None:
    """
    Register a callable that performs the actual FCM send.
    The callable receives a JSON string and returns True on success.
    """
    global _sender, _sender_name
    with _lock:
        _sender = sender_callable
        _sender_name = name
    _log(f"✅ FCM sender registered: {name}")


def unregister_sender() -> None:
    global _sender, _sender_name
    with _lock:
        _sender = None
        _sender_name = None
    _log("FCM sender unregistered")


def is_ready() -> bool:
    with _lock:
        return _sender is not None


def sender_name() -> Optional[str]:
    with _lock:
        return _sender_name


# ───────────────────────────────────────────────────────────────
#  Payload builders
# ───────────────────────────────────────────────────────────────

def build_command_payload(device_id: str,
                          command: str,
                          payload: Optional[str] = None,
                          ttl_ms: Optional[int] = None) -> Dict[str, Any]:
    """
    Build the JSON payload for a command message.
    Kotlin side is responsible for converting this into the FCM v1
    "message" envelope and POSTing to:
       https://fcm.googleapis.com/v1/projects/<project>/messages:send
    """
    data = {
        "type": "command",
        "command": command,
        "device_id": device_id,
        "sent_at": int(time.time() * 1000),
    }
    if payload is not None:
        data["payload"] = payload
    if ttl_ms is not None:
        data["ttl_ms"] = int(ttl_ms)
    return data


def build_alert_payload(device_id: str,
                        alert_type: str,
                        message: str,
                        severity: str = "info") -> Dict[str, Any]:
    data = {
        "type": "alert",
        "alert_type": alert_type,
        "severity": severity,
        "message": message,
        "device_id": device_id,
        "sent_at": int(time.time() * 1000),
    }
    return data


def build_heartbeat_payload(device_id: str) -> Dict[str, Any]:
    return {
        "type": "heartbeat",
        "device_id": device_id,
        "sent_at": int(time.time() * 1000),
    }


# ───────────────────────────────────────────────────────────────
#  Send
# ───────────────────────────────────────────────────────────────

def _inc(key: str) -> None:
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + 1


def send_raw(payload: Dict[str, Any]) -> bool:
    """
    Send a payload dict via the registered Kotlin sender.
    Returns True on success, False otherwise.
    """
    with _lock:
        sender = _sender
    if sender is None:
        _inc("bridge_missing")
        _log(f"⚠️ No FCM sender registered — dropped: "
             f"{payload.get('type', '?')}")
        return False

    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        _inc("failed")
        _log(f"❌ Payload serialization failed: {e}")
        return False

    try:
        ok = bool(sender(body))
    except Exception as e:
        ok = False
        _log(f"❌ Sender raised: {e}")

    if ok:
        _inc("sent")
        _log(f"✅ FCM sent → {payload.get('device_id')} "
             f"[{payload.get('type')}]")
    else:
        _inc("failed")
        _log(f"❌ FCM send failed → {payload.get('device_id')}")

    return ok


def send_command(device_id: str,
                 command: str,
                 payload: Optional[str] = None,
                 ttl_ms: Optional[int] = None) -> bool:
    data = build_command_payload(device_id, command, payload, ttl_ms)
    return send_raw(data)


def send_alert(device_id: str,
               alert_type: str,
               message: str,
               severity: str = "info") -> bool:
    data = build_alert_payload(device_id, alert_type, message, severity)
    return send_raw(data)


def send_heartbeat(device_id: str) -> bool:
    return send_raw(build_heartbeat_payload(device_id))


# ───────────────────────────────────────────────────────────────
#  Diagnostics
# ───────────────────────────────────────────────────────────────

def get_stats() -> Dict[str, Any]:
    with _stats_lock:
        s = dict(_stats)
    s["ready"] = is_ready()
    s["sender_name"] = sender_name()
    return s


def reset_stats() -> None:
    with _stats_lock:
        for k in _stats:
            _stats[k] = 0


def _log(msg: str) -> None:
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [FCM] {msg}", flush=True)
