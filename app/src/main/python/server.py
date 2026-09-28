# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Flask HTTP Server
# ═══════════════════════════════════════════════════════════════

import os
import sys
import time
import json
import math
import threading
import traceback
from datetime import datetime
from functools import wraps
from typing import Optional, Dict, Any, List, Tuple

try:
    from flask import Flask, request, jsonify, Response, g
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

import config as cfg
import database as db
import validators as vd
import migrations as mig
import websocket_server as ws


_app: Optional["Flask"] = None
_thread: Optional[threading.Thread] = None
_running: bool = False
_start_time: float = time.time()
_lock = threading.Lock()

_metrics: Dict[str, Any] = {
    "requests_total": 0,
    "requests_by_endpoint": {},
    "locations_saved": 0,
    "commands_queued": 0,
    "errors_total": 0,
    "started_at": int(time.time()),
}
_metrics_lock = threading.Lock()

_rate_store: Dict[str, List[float]] = {}
_rate_lock = threading.Lock()


def _log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [SERVER/{level}] {msg}", flush=True)


def log_info(msg: str) -> None:
    _log("INFO", msg)


def log_warn(msg: str) -> None:
    _log("WARN", msg)


def log_error(msg: str) -> None:
    _log("ERROR", msg)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _inc_metric(key: str, endpoint: Optional[str] = None) -> None:
    with _metrics_lock:
        if key in _metrics and isinstance(_metrics[key], int):
            _metrics[key] += 1
        if endpoint:
            d = _metrics["requests_by_endpoint"]
            d[endpoint] = d.get(endpoint, 0) + 1


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _auth_admin() -> bool:
    if not cfg.ADMIN_KEY:
        return True
    key = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    return key == cfg.ADMIN_KEY


def _auth_device(device_id: str) -> bool:
    presented = request.headers.get("X-Device-Key")
    device = db.get_device(device_id)
    if device and device.get("device_key"):
        return presented == device["device_key"]
    if cfg.DEVICE_DEFAULT_KEY:
        return presented == cfg.DEVICE_DEFAULT_KEY
    return True


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _auth_admin():
            _inc_metric("errors_total")
            return jsonify({"error": "unauthorized_admin"}), 401
        return f(*args, **kwargs)
    return wrapper


def require_device(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        device_id = kwargs.get("device_id")
        if not device_id:
            data = request.get_json(silent=True) or {}
            device_id = data.get("device_id")
        if not device_id:
            return jsonify({"error": "device_id_required"}), 400
        if not _auth_device(device_id):
            _inc_metric("errors_total")
            return jsonify({"error": "unauthorized_device"}), 401
        return f(*args, **kwargs)
    return wrapper


def _rate_limit_key() -> str:
    admin = request.headers.get("X-Admin-Key") or ""
    device = request.headers.get("X-Device-Key") or ""
    ip = request.remote_addr or "unknown"
    return f"a:{admin}|d:{device}|ip:{ip}"


def _check_rate_limit() -> bool:
    if not cfg.RATE_LIMIT_ENABLED:
        return True
    key = _rate_limit_key()
    now = time.time()
    window = cfg.RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        stamps = _rate_store.get(key, [])
        stamps = [t for t in stamps if now - t < window]
        if len(stamps) >= cfg.RATE_LIMIT_MAX_REQUESTS:
            _rate_store[key] = stamps
            return False
        stamps.append(now)
        _rate_store[key] = stamps
    return True


def rate_limit(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _check_rate_limit():
            _inc_metric("errors_total")
            return jsonify({
                "error": "rate_limit_exceeded",
                "retry_after": cfg.RATE_LIMIT_WINDOW_SECONDS,
            }), 429
        return f(*args, **kwargs)
    return wrapper


def _json_error(msg: str, code: int = 400) -> Tuple[Response, int]:
    _inc_metric("errors_total")
    return jsonify({"error": msg}), code


def _require_json() -> Optional[Dict[str, Any]]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def _publish_location_safe(device_id: str, row_id: int, data: Dict[str, Any]) -> None:
    try:
        ws.publish_location(device_id, {
            "id": row_id,
            "lat": data["lat"],
            "lon": data["lon"],
            "accuracy": data.get("accuracy"),
            "speed": data.get("speed"),
            "battery": data.get("battery"),
            "source": data.get("source"),
            "timestamp": data.get("timestamp") or _now_ms(),
        })
    except Exception as e:
        log_warn(f"websocket publish (location) failed: {e}")


def _publish_command_safe(device_id: str, command_id: str,
                          status: str, result: Optional[str] = None) -> None:
    try:
        ws.publish_command_update(device_id, command_id, status, result)
    except Exception as e:
        log_warn(f"websocket publish (command) failed: {e}")


def _create_app() -> "Flask":
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    app.config["MAX_CONTENT_LENGTH"] = cfg.SERVER_MAX_CONTENT_MB * 1024 * 1024

    @app.route("/")
    def index():
        _inc_metric("requests_total", "index")
        return jsonify({
            "status": "ok",
            "service": "ULTRA Family Tracker",
            "version": "5.0.0",
            "api": cfg.API_PREFIX,
            "time": _now_ms(),
            "uptime": int(time.time() - _start_time),
            "python": sys.version.split()[0],
        })

    @app.route("/health")
    def health():
        _inc_metric("requests_total", "health")
        db_ok = db.ping()
        return jsonify({
            "status": "healthy" if db_ok else "degraded",
            "db": db_ok,
            "uptime": int(time.time() - _start_time),
            "db_size_mb": db.get_db_size_mb(),
        })

    @app.route("/metrics")
    @require_admin
    def metrics():
        _inc_metric("requests_total", "metrics")
        with _metrics_lock:
            snapshot = dict(_metrics)
        return jsonify({
            **snapshot,
            "uptime": int(time.time() - _start_time),
            "db_size_mb": db.get_db_size_mb(),
        })

    @app.route(f"{cfg.API_PREFIX}/devices/register", methods=["POST"])
    @rate_limit
    def register():
        _inc_metric("requests_total", "register")
        data = _require_json()
        if data is None:
            return _json_error("invalid_json")
        ok, err = vd.validate_register_payload(data)
        if not ok:
            return _json_error(err)
        db.register_device(
            device_id=data["device_id"],
            name=data.get("name"),
            model=data.get("model"),
            android_version=data.get("android_version"),
        )
        log_info(f"Registered: {data['device_id']}")
        return jsonify({
            "status": "registered",
            "device_id": data["device_id"],
            "server_time": _now_ms(),
        })

    @app.route(f"{cfg.API_PREFIX}/devices", methods=["GET"])
    @require_admin
    def list_devices():
        _inc_metric("requests_total", "list_devices")
        only_active = request.args.get("active", "0") in ("1", "true", "yes")
        return jsonify({"devices": db.list_devices(only_active=only_active)})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>", methods=["GET"])
    @require_admin
    def get_device(device_id):
        _inc_metric("requests_total", "get_device")
        d = db.get_device(device_id)
        if not d:
            return _json_error("not_found", 404)
        return jsonify(d)

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>", methods=["DELETE"])
    @require_admin
    def delete_device(device_id):
        _inc_metric("requests_total", "delete_device")
        db.delete_device(device_id)
        log_info(f"Deleted: {device_id}")
        return jsonify({"status": "deleted"})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/locations", methods=["POST"])
    @require_device
    @rate_limit
    def post_location(device_id):
        _inc_metric("requests_total", "post_location")
        data = _require_json()
        if data is None:
            return _json_error("invalid_json")
        data["device_id"] = device_id
        ok, err = vd.validate_location_payload(data)
        if not ok:
            return _json_error(err)
        row_id = db.save_location(device_id, data)
        _inc_metric("locations_saved")
        _publish_location_safe(device_id, row_id, data)
        spd = (data.get("speed") or 0) * 3.6
        log_info(
            f"{device_id}: {data['lat']:.5f},{data['lon']:.5f} | "
            f"{spd:.1f}km/h | bat:{data.get('battery', '?')}% | id={row_id}"
        )
        return jsonify({"status": "saved", "id": row_id, "server_time": _now_ms()})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/locations/batch", methods=["POST"])
    @require_device
    @rate_limit
    def post_location_batch(device_id):
        _inc_metric("requests_total", "post_location_batch")
        data = _require_json()
        if data is None:
            return _json_error("invalid_json")
        data["device_id"] = device_id
        ok, err = vd.validate_location_batch_payload(data)
        if not ok:
            return _json_error(err)
        saved = 0
        for loc in data["locations"]:
            loc.setdefault("timestamp", _now_ms())
            try:
                row_id = db.save_location(device_id, loc)
                saved += 1
                _publish_location_safe(device_id, row_id, loc)
            except Exception as e:
                log_warn(f"Batch item error: {e}")
        _inc_metric("locations_saved")
        log_info(f"Batch: {device_id} {saved}/{len(data['locations'])}")
        return jsonify({
            "status": "saved",
            "count": saved,
            "received": len(data["locations"]),
        })

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/locations/last", methods=["GET"])
    @require_admin
    def last_location(device_id):
        _inc_metric("requests_total", "last_location")
        loc = db.get_last_location(device_id)
        if not loc:
            return _json_error("no_location", 404)
        return jsonify(loc)

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/locations", methods=["GET"])
    @require_admin
    def get_locations(device_id):
        _inc_metric("requests_total", "get_locations")
        ok, hours, err = vd.parse_int_arg(request.args, "hours", 24, 1, 720)
        if not ok:
            return _json_error(err)
        ok, limit, err = vd.parse_int_arg(request.args, "limit", 5000, 1, 50000)
        if not ok:
            return _json_error(err)
        since = _now_ms() - hours * 3600 * 1000
        rows = db.get_locations(device_id, since=since, limit=limit)
        total_m = 0.0
        for i in range(1, len(rows)):
            total_m += _haversine(
                rows[i - 1]["lat"], rows[i - 1]["lon"],
                rows[i]["lat"], rows[i]["lon"],
            )
        return jsonify({
            "device_id": device_id,
            "hours": hours,
            "count": len(rows),
            "total_distance_m": round(total_m, 2),
            "total_distance_km": round(total_m / 1000, 2),
            "locations": rows,
        })

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/commands", methods=["POST"])
    @require_admin
    def post_command(device_id):
        _inc_metric("requests_total", "post_command")
        data = _require_json()
        if data is None:
            return _json_error("invalid_json")
        ok, err = vd.validate_command_payload(data)
        if not ok:
            return _json_error(err)
        cmd_id = db.add_command(
            device_id=device_id,
            command=data["command"],
            payload=data.get("payload"),
            ttl_ms=data.get("ttl_ms"),
        )
        _inc_metric("commands_queued")
        _publish_command_safe(device_id, cmd_id, "queued")
        log_info(f"Command queued: {device_id} -> {data['command']} ({cmd_id[:8]})")
        return jsonify({"status": "queued", "command_id": cmd_id})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/commands/pending", methods=["GET"])
    @require_device
    def pending_commands(device_id):
        _inc_metric("requests_total", "pending_commands")
        cmds = db.get_pending_commands(device_id)
        if cmds:
            log_info(f"Sent {len(cmds)} command(s) -> {device_id}")
        return jsonify({"commands": cmds, "count": len(cmds)})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/commands/<command_id>/ack", methods=["POST"])
    @require_device
    def ack_command(device_id, command_id):
        _inc_metric("requests_total", "ack_command")
        data = _require_json() or {}
        status = data.get("status", "success")
        if status not in ("success", "failed"):
            return _json_error("status must be 'success' or 'failed'")
        db.mark_command_result(
            command_id=command_id,
            status=status,
            result=data.get("result"),
            error=data.get("error"),
        )
        _publish_command_safe(device_id, command_id, status, data.get("result"))
        log_info(f"Command ack: {device_id} / {command_id[:8]} -> {status}")
        return jsonify({"status": "acked"})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/commands/history", methods=["GET"])
    @require_admin
    def command_history(device_id):
        _inc_metric("requests_total", "command_history")
        ok, limit, err = vd.parse_int_arg(request.args, "limit", 100, 1, 1000)
        if not ok:
            return _json_error(err)
        return jsonify({"history": db.get_command_history(device_id, limit=limit)})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/geofences", methods=["POST"])
    @require_admin
    def add_geofence(device_id):
        _inc_metric("requests_total", "add_geofence")
        data = _require_json()
        if data is None:
            return _json_error("invalid_json")
        ok, err = vd.validate_geofence_payload(data)
        if not ok:
            return _json_error(err)
        gid = db.add_geofence(
            device_id=device_id,
            name=data["name"],
            lat=data["lat"],
            lon=data["lon"],
            radius=data.get("radius", 200),
        )
        log_info(f"Geofence added: {device_id} / {data['name']}")
        return jsonify({"status": "added", "id": gid})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/geofences", methods=["GET"])
    @require_admin
    def list_geofences(device_id):
        _inc_metric("requests_total", "list_geofences")
        return jsonify({"geofences": db.get_geofences(device_id)})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/geofences/<int:gid>", methods=["DELETE"])
    @require_admin
    def delete_geofence(device_id, gid):
        _inc_metric("requests_total", "delete_geofence")
        db.delete_geofence(gid)
        log_info(f"Geofence deleted: {gid}")
        return jsonify({"status": "deleted"})

    @app.route(f"{cfg.API_PREFIX}/devices/<device_id>/alerts", methods=["GET"])
    @require_admin
    def list_alerts(device_id):
        _inc_metric("requests_total", "list_alerts")
        ok, limit, err = vd.parse_int_arg(request.args, "limit", 100, 1, 1000)
        if not ok:
            return _json_error(err)
        return jsonify({"alerts": db.get_alerts(device_id, limit=limit)})

    @app.route(f"{cfg.API_PREFIX}/server/stats", methods=["GET"])
    @require_admin
    def server_stats():
        _inc_metric("requests_total", "server_stats")
        with _metrics_lock:
            snapshot = dict(_metrics)
        return jsonify({
            "version": "5.0.0",
            "uptime_seconds": int(time.time() - _start_time),
            "db": db.get_stats_summary(),
            "metrics": snapshot,
        })

    @app.errorhandler(404)
    def _404(e):
        return jsonify({"error": "not_found"}), 404

    @app.errorhandler(405)
    def _405(e):
        return jsonify({"error": "method_not_allowed"}), 405

    @app.errorhandler(413)
    def _413(e):
        return jsonify({"error": "payload_too_large"}), 413

    @app.errorhandler(500)
    def _500(e):
        _inc_metric("errors_total")
        return jsonify({"error": "internal_error"}), 500

    return app


def start_server(port: int = 0, debug: bool = False) -> str:
    global _app, _thread, _running
    with _lock:
        if _running:
            log_warn("Server already running")
            return "already_running"
        if not FLASK_AVAILABLE:
            log_error("Flask is not available")
            return "flask_not_available"
        try:
            db.init_db()
            log_info("database initialized")
            conn = db._get_conn()
            report = mig.ensure_schema(conn)
            if report.get("failed"):
                log_error(f"Migration failed: {report.get('error')}")
                return f"error: migration_failed: {report.get('error')}"
            log_info(f"schema at v{report.get('to_version')}")
            _app = _create_app()
            actual_port = port or cfg.SERVER_PORT

            def _run():
                global _running
                try:
                    _running = True
                    log_info(f"Server listening on 0.0.0.0:{actual_port}")
                    _app.run(
                        host="0.0.0.0",
                        port=actual_port,
                        debug=debug,
                        threaded=True,
                        use_reloader=False,
                    )
                except Exception as e:
                    log_error(f"Server crashed: {e}")
                    log_error(traceback.format_exc())
                finally:
                    _running = False

            _thread = threading.Thread(target=_run, daemon=True)
            _thread.start()
            time.sleep(2.0)
            if _running:
                log_info("Server ready")
                return "started"
            log_error("Server failed to start")
            return "failed"
        except Exception as e:
            log_error(f"start_server failed: {e}")
            log_error(traceback.format_exc())
            return f"error: {e}"


def stop_server() -> str:
    global _running
    _running = False
    log_info("Server stop requested")
    return "stopping"


def get_status() -> Dict[str, Any]:
    with _metrics_lock:
        snapshot = dict(_metrics)
    return {
        "running": _running,
        "uptime": int(time.time() - _start_time),
        "port": cfg.SERVER_PORT,
        "api": cfg.API_PREFIX,
        "db_ok": db.ping(),
        "metrics": snapshot,
    }


def get_version() -> str:
    return "5.0.0"


def ping() -> str:
    return "pong"


if __name__ == "__main__":
    log_info("Standalone Mode")
    result = start_server()
    log_info(f"start_server -> {result}")
    try:
        while True:
            time.sleep(60)
            log_info(f"heartbeat {get_status()}")
    except KeyboardInterrupt:
        log_info("Shutting down...")
        stop_server()
