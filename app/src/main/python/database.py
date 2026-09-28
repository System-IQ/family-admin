# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Database Layer
# ═══════════════════════════════════════════════════════════════

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Iterator

import config as cfg


# ───────────────────────────────────────────────────────────────
#  Connection management
# ───────────────────────────────────────────────────────────────

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            cfg.DB_PATH,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")
        _local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = _get_conn()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def close_all() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


# ───────────────────────────────────────────────────────────────
#  Schema
# ───────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id        TEXT PRIMARY KEY,
    name             TEXT,
    model            TEXT,
    android_version  TEXT,
    device_key       TEXT,
    battery          INTEGER,
    is_charging      INTEGER,
    network          TEXT,
    last_lat         REAL,
    last_lon         REAL,
    last_seen        INTEGER,
    registered_at    INTEGER,
    active           INTEGER NOT NULL DEFAULT 1,
    CHECK (battery IS NULL OR (battery >= 0 AND battery <= 100))
);

CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_devices_active ON devices(active);

CREATE TABLE IF NOT EXISTS locations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    timestamp   INTEGER NOT NULL,
    accuracy    REAL,
    altitude    REAL,
    speed       REAL,
    bearing     REAL,
    source      TEXT,
    battery     INTEGER,
    synced      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE,
    CHECK (lat >= -90 AND lat <= 90),
    CHECK (lon >= -180 AND lon <= 180),
    CHECK (accuracy IS NULL OR accuracy >= 0)
);

CREATE INDEX IF NOT EXISTS idx_locations_dev_time ON locations(device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_locations_time ON locations(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_locations_event ON locations(event_id);
CREATE INDEX IF NOT EXISTS idx_locations_synced ON locations(synced);

CREATE TABLE IF NOT EXISTS locations_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL UNIQUE,
    device_id   TEXT NOT NULL,
    payload     TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_queue_created ON locations_queue(created_at);

CREATE TABLE IF NOT EXISTS commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id   TEXT NOT NULL UNIQUE,
    device_id    TEXT NOT NULL,
    command      TEXT NOT NULL,
    payload      TEXT,
    status       TEXT NOT NULL DEFAULT 'created',
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER,
    executed_at  INTEGER,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_commands_dev_status ON commands(device_id, status);
CREATE INDEX IF NOT EXISTS idx_commands_created ON commands(created_at DESC);

CREATE TABLE IF NOT EXISTS command_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id   TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    command      TEXT NOT NULL,
    result       TEXT,
    error        TEXT,
    executed_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_dev ON command_history(device_id, executed_at DESC);

CREATE TABLE IF NOT EXISTS stops (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id        TEXT NOT NULL,
    lat              REAL NOT NULL,
    lon              REAL NOT NULL,
    start_time       INTEGER NOT NULL,
    end_time         INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL,
    radius           REAL NOT NULL DEFAULT 50.0,
    address          TEXT,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stops_dev_time ON stops(device_id, start_time DESC);

CREATE TABLE IF NOT EXISTS stats_daily (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id          TEXT NOT NULL,
    date               TEXT NOT NULL,
    distance_m         REAL NOT NULL DEFAULT 0,
    moving_seconds     INTEGER NOT NULL DEFAULT 0,
    stopped_seconds    INTEGER NOT NULL DEFAULT 0,
    max_speed_kmh      REAL NOT NULL DEFAULT 0,
    avg_speed_kmh      REAL NOT NULL DEFAULT 0,
    stop_count         INTEGER NOT NULL DEFAULT 0,
    point_count        INTEGER NOT NULL DEFAULT 0,
    first_seen         INTEGER,
    last_seen          INTEGER,
    UNIQUE (device_id, date),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_stats_dev_date ON stats_daily(device_id, date DESC);

CREATE TABLE IF NOT EXISTS geofences (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  TEXT NOT NULL,
    name       TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    radius     INTEGER NOT NULL DEFAULT 200,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE,
    CHECK (radius >= 10 AND radius <= 100000)
);

CREATE INDEX IF NOT EXISTS idx_geofences_dev_active ON geofences(device_id, active);

CREATE TABLE IF NOT EXISTS geofence_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    geofence_id INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    timestamp   INTEGER NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE,
    FOREIGN KEY (geofence_id) REFERENCES geofences(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_geo_events_dev_time ON geofence_events(device_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    alert_type  TEXT NOT NULL,
    severity    TEXT NOT NULL DEFAULT 'info',
    message     TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alerts_dev_created ON alerts(device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_delivered ON alerts(delivered);

CREATE TABLE IF NOT EXISTS battery_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    level       INTEGER,
    is_charging INTEGER,
    temperature REAL,
    timestamp   INTEGER NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE,
    CHECK (level IS NULL OR (level >= 0 AND level <= 100))
);

CREATE INDEX IF NOT EXISTS idx_battery_dev_time ON battery_log(device_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS device_state (
    device_id       TEXT PRIMARY KEY,
    connection      INTEGER NOT NULL DEFAULT 1,
    gps_enabled     INTEGER NOT NULL DEFAULT 1,
    battery         INTEGER,
    is_charging     INTEGER,
    network         TEXT,
    last_heartbeat  INTEGER,
    updated_at      INTEGER NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    pin_hash    TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pins_dev_created ON pins(device_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pins_active ON pins(device_id, revoked, expires_at);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,
    actor       TEXT,
    action      TEXT NOT NULL,
    target      TEXT,
    result      TEXT,
    details     TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor, timestamp DESC);
"""


# ───────────────────────────────────────────────────────────────
#  Utilities
# ───────────────────────────────────────────────────────────────

def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, _now_ms()),
    )


def init_db() -> None:
    conn = _get_conn()
    conn.executescript(SCHEMA_SQL)
    current = _get_schema_version(conn)
    if current < cfg.DB_SCHEMA_VERSION:
        with transaction():
            _set_schema_version(conn, cfg.DB_SCHEMA_VERSION)


# ───────────────────────────────────────────────────────────────
#  Devices
# ───────────────────────────────────────────────────────────────

def register_device(
    device_id: str,
    name: Optional[str] = None,
    model: Optional[str] = None,
    android_version: Optional[str] = None,
) -> None:
    now = _now_ms()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO devices
                (device_id, name, model, android_version, last_seen, registered_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                name            = COALESCE(excluded.name, devices.name),
                model           = COALESCE(excluded.model, devices.model),
                android_version = COALESCE(excluded.android_version, devices.android_version),
                last_seen       = excluded.last_seen,
                active          = 1
            """,
            (device_id, name, model, android_version, now, now),
        )


def update_device_status(
    device_id: str,
    battery: Optional[int] = None,
    charging: Optional[bool] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> None:
    now = _now_ms()
    with transaction() as conn:
        conn.execute(
            """
            UPDATE devices SET
                battery     = COALESCE(?, battery),
                is_charging = COALESCE(?, is_charging),
                last_lat    = COALESCE(?, last_lat),
                last_lon    = COALESCE(?, last_lon),
                last_seen   = ?
            WHERE device_id = ?
            """,
            (
                battery,
                1 if charging is True else (0 if charging is False else None),
                lat, lon, now, device_id,
            ),
        )


def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_devices(only_active: bool = False) -> List[Dict[str, Any]]:
    conn = _get_conn()
    if only_active:
        cur = conn.execute(
            "SELECT * FROM devices WHERE active = 1 ORDER BY last_seen DESC"
        )
    else:
        cur = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC")
    return [dict(r) for r in cur.fetchall()]


def delete_device(device_id: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))


# ───────────────────────────────────────────────────────────────
#  Locations
# ───────────────────────────────────────────────────────────────

def save_location(device_id: str, data: Dict[str, Any]) -> int:
    event_id = data.get("event_id") or ""
    if not event_id:
        import uuid
        event_id = str(uuid.uuid4())
    ts = data.get("timestamp") or _now_ms()

    with transaction() as conn:
        cur = conn.execute(
            "SELECT id FROM locations WHERE event_id = ? LIMIT 1", (event_id,)
        )
        existing = cur.fetchone()
        if existing:
            return int(existing["id"])

        cur = conn.execute(
            """
            INSERT INTO locations
                (event_id, device_id, lat, lon, timestamp, accuracy,
                 altitude, speed, bearing, source, battery, synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, device_id,
                float(data["lat"]), float(data["lon"]), int(ts),
                data.get("accuracy"), data.get("altitude"),
                data.get("speed"), data.get("bearing"),
                data.get("source", "unknown"), data.get("battery"),
                int(data.get("synced", 1)),
            ),
        )
        row_id = int(cur.lastrowid)

        conn.execute(
            """
            UPDATE devices SET
                last_lat  = ?, last_lon  = ?, last_seen = ?,
                battery   = COALESCE(?, battery)
            WHERE device_id = ?
            """,
            (data["lat"], data["lon"], int(ts), data.get("battery"), device_id),
        )
        return row_id


def get_last_location(device_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.execute(
        "SELECT * FROM locations WHERE device_id = ? ORDER BY timestamp DESC LIMIT 1",
        (device_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def get_locations(
    device_id: str,
    since: Optional[int] = None,
    until: Optional[int] = None,
    limit: int = 5000,
) -> List[Dict[str, Any]]:
    conn = _get_conn()
    q = "SELECT * FROM locations WHERE device_id = ?"
    params: List[Any] = [device_id]
    if since is not None:
        q += " AND timestamp >= ?"
        params.append(since)
    if until is not None:
        q += " AND timestamp <= ?"
        params.append(until)
    q += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    cur = conn.execute(q, params)
    return [dict(r) for r in cur.fetchall()]


def count_locations(device_id: str, since: Optional[int] = None) -> int:
    conn = _get_conn()
    if since is None:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM locations WHERE device_id = ?", (device_id,)
        )
    else:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM locations WHERE device_id = ? AND timestamp >= ?",
            (device_id, since),
        )
    return int(cur.fetchone()["n"])


# ───────────────────────────────────────────────────────────────
#  Commands
# ───────────────────────────────────────────────────────────────

def add_command(device_id: str, command: str, payload: Optional[str] = None,
                ttl_ms: Optional[int] = None) -> str:
    import uuid
    command_id = str(uuid.uuid4())
    now = _now_ms()
    expires_at = now + ttl_ms if ttl_ms else None

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO commands
                (command_id, device_id, command, payload, status, created_at, expires_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?)
            """,
            (command_id, device_id, command, payload, now, expires_at),
        )
    return command_id


def get_pending_commands(device_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    now = _now_ms()
    with transaction() as conn:
        conn.execute(
            """
            UPDATE commands SET status = 'expired'
            WHERE device_id = ? AND status = 'queued'
              AND expires_at IS NOT NULL AND expires_at < ?
            """,
            (device_id, now),
        )
        cur = conn.execute(
            """
            SELECT * FROM commands
            WHERE device_id = ? AND status = 'queued'
            ORDER BY created_at ASC LIMIT ?
            """,
            (device_id, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            ids = [r["id"] for r in rows]
            q = "UPDATE commands SET status = 'sent' WHERE id IN ({})".format(
                ",".join("?" * len(ids))
            )
            conn.execute(q, ids)
    return rows


def mark_command_result(
    command_id: str, status: str,
    result: Optional[str] = None, error: Optional[str] = None,
) -> None:
    now = _now_ms()
    with transaction() as conn:
        conn.execute(
            "UPDATE commands SET status = ?, executed_at = ? WHERE command_id = ?",
            (status, now, command_id),
        )
        conn.execute(
            """
            INSERT INTO command_history
                (command_id, device_id, command, result, error, executed_at)
            SELECT command_id, device_id, command, ?, ?, ?
            FROM commands WHERE command_id = ?
            """,
            (result, error, now, command_id),
        )


def get_command_history(device_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.execute(
        """
        SELECT * FROM command_history
        WHERE device_id = ? ORDER BY executed_at DESC LIMIT ?
        """,
        (device_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]


# ───────────────────────────────────────────────────────────────
#  Geofences
# ───────────────────────────────────────────────────────────────

def add_geofence(device_id: str, name: str, lat: float, lon: float,
                 radius: int = 200) -> int:
    now = _now_ms()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO geofences (device_id, name, lat, lon, radius, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (device_id, name, lat, lon, radius, now),
        )
        return int(cur.lastrowid)


def get_geofences(device_id: str, only_active: bool = True) -> List[Dict[str, Any]]:
    conn = _get_conn()
    if only_active:
        cur = conn.execute(
            "SELECT * FROM geofences WHERE device_id = ? AND active = 1", (device_id,)
        )
    else:
        cur = conn.execute("SELECT * FROM geofences WHERE device_id = ?", (device_id,))
    return [dict(r) for r in cur.fetchall()]


def delete_geofence(geofence_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM geofences WHERE id = ?", (geofence_id,))


# ───────────────────────────────────────────────────────────────
#  Alerts
# ───────────────────────────────────────────────────────────────

def add_alert(device_id: str, alert_type: str, message: str,
              severity: str = "info") -> int:
    now = _now_ms()
    with transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts (device_id, alert_type, severity, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (device_id, alert_type, severity, message, now),
        )
        return int(cur.lastrowid)


def get_alerts(device_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.execute(
        "SELECT * FROM alerts WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
        (device_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]


# ───────────────────────────────────────────────────────────────
#  Battery log
# ───────────────────────────────────────────────────────────────

def log_battery(device_id: str, level: int, is_charging: bool,
                temperature: Optional[float] = None) -> None:
    now = _now_ms()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO battery_log
                (device_id, level, is_charging, temperature, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (device_id, level, 1 if is_charging else 0, temperature, now),
        )


# ───────────────────────────────────────────────────────────────
#  Cleanup
# ───────────────────────────────────────────────────────────────

def cleanup_old_data() -> Dict[str, int]:
    now = _now_ms()
    day_ms = 24 * 3600 * 1000
    deleted: Dict[str, int] = {}

    with transaction() as conn:
        cut = now - cfg.RETENTION_LOCATIONS_DAYS * day_ms
        cur = conn.execute("DELETE FROM locations WHERE timestamp < ?", (cut,))
        deleted["locations"] = cur.rowcount

        cut = now - cfg.RETENTION_BATTERY_DAYS * day_ms
        cur = conn.execute("DELETE FROM battery_log WHERE timestamp < ?", (cut,))
        deleted["battery_log"] = cur.rowcount

        cut = now - cfg.RETENTION_ALERTS_DAYS * day_ms
        cur = conn.execute("DELETE FROM alerts WHERE created_at < ?", (cut,))
        deleted["alerts"] = cur.rowcount

        cut = now - cfg.RETENTION_AUDIT_DAYS * day_ms
        cur = conn.execute("DELETE FROM audit_log WHERE timestamp < ?", (cut,))
        deleted["audit_log"] = cur.rowcount

    return deleted


def vacuum() -> None:
    conn = _get_conn()
    conn.execute("VACUUM")


# ───────────────────────────────────────────────────────────────
#  Utilities
# ───────────────────────────────────────────────────────────────

def get_db_size_mb() -> float:
    try:
        return round(os.path.getsize(cfg.DB_PATH) / (1024 * 1024), 3)
    except OSError:
        return 0.0


def get_stats_summary() -> Dict[str, Any]:
    conn = _get_conn()
    out: Dict[str, Any] = {}
    for table in ("devices", "locations", "commands", "stops",
                  "alerts", "battery_log", "geofences"):
        try:
            cur = conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
            out[table] = int(cur.fetchone()["n"])
        except sqlite3.OperationalError:
            out[table] = -1
    out["db_size_mb"] = get_db_size_mb()
    return out


def ping() -> bool:
    try:
        conn = _get_conn()
        conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False
