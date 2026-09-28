# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Schema Migrations
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Apply incremental schema changes to existing databases
#    without data loss.
#
#  Design:
#    - Each migration is a Python function: `migrate_<version>`
#    - Migrations run in order: 1 → 2 → 3 → ...
#    - `schema_version` table (created by database.py) tracks current
#    - All migrations run inside a single transaction per migration
#    - Migrations are idempotent whenever possible
#
#  Contract:
#    Each migration function receives a sqlite3.Connection and may:
#      - ALTER TABLE
#      - CREATE TABLE
#      - INSERT/UPDATE/DELETE
#      - Run any SQL via conn.execute / conn.executescript
#    It must NOT:
#      - Open its own connection
#      - Modify schema_version directly
#      - Raise on already-applied changes (guard with IF NOT EXISTS etc.)
# ═══════════════════════════════════════════════════════════════

import sqlite3
import time
from typing import Callable, Dict, List, Tuple

import config as cfg


# ───────────────────────────────────────────────────────────────
#  Helpers
# ───────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Read max version from schema_version. 0 if table empty/missing."""
    try:
        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.OperationalError:
        return 0


def _record_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (version, _now_ms()),
    )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if the given column exists in the table."""
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cur.fetchall())
    except sqlite3.OperationalError:
        return False


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


# ───────────────────────────────────────────────────────────────
#  MIGRATIONS
#  Each function is idempotent and only applies the change
#  if it hasn't been applied yet.
# ───────────────────────────────────────────────────────────────

def migrate_1(conn: sqlite3.Connection) -> None:
    """
    v1: Initial schema.
    This migration corresponds to what database.py already creates.
    It exists so that 'migrations applied' matches 'schema_version = 1'.
    Nothing to do here — the base schema was created by init_db().
    """
    # No-op by design.
    pass


def migrate_2(conn: sqlite3.Connection) -> None:
    """
    v2: Add 'geofence_events.event_type' CHECK constraint (placeholder).
    When v2 schema is required, change this body.
    For now, it's a placeholder so the framework is exercised end-to-end.
    """
    # Placeholder for future schema change.
    pass


# ───────────────────────────────────────────────────────────────
#  Registry
# ───────────────────────────────────────────────────────────────

# Ordered list: (version, function)
MIGRATIONS: List[Tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, migrate_1),
    (2, migrate_2),
]


def get_registered_versions() -> List[int]:
    return [v for v, _ in MIGRATIONS]


def get_latest_version() -> int:
    return max(get_registered_versions()) if MIGRATIONS else 0


# ───────────────────────────────────────────────────────────────
#  Runner
# ───────────────────────────────────────────────────────────────

def run_pending_migrations(conn: sqlite3.Connection) -> Dict[str, object]:
    """
    Apply all migrations whose version > current.
    Returns a report:
        {
            "from_version": int,
            "to_version": int,
            "applied": [int, ...],
            "skipped": [int, ...],
            "failed":  Optional[int],
            "error":   Optional[str],
        }
    """
    from_version = _get_current_version(conn)
    applied: List[int] = []
    skipped: List[int] = []

    report: Dict[str, object] = {
        "from_version": from_version,
        "to_version": from_version,
        "applied": applied,
        "skipped": skipped,
        "failed": None,
        "error": None,
    }

    for version, func in MIGRATIONS:
        if version <= from_version:
            skipped.append(version)
            continue

        try:
            conn.execute("BEGIN")
            func(conn)
            _record_version(conn, version)
            conn.execute("COMMIT")
            applied.append(version)
        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            report["failed"] = version
            report["error"] = str(e)
            report["to_version"] = _get_current_version(conn)
            return report

    report["to_version"] = _get_current_version(conn)
    return report


# ───────────────────────────────────────────────────────────────
#  Public entry point (called after init_db in server startup)
# ───────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> Dict[str, object]:
    """
    High-level entry:
      1. Ensures schema_version table exists (safety net).
      2. Runs pending migrations.
    Safe to call multiple times.
    """
    # Safety: create schema_version if missing (usually created by init_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  INTEGER NOT NULL
        )
        """
    )
    return run_pending_migrations(conn)


# ───────────────────────────────────────────────────────────────
#  Introspection helpers (for diagnostics)
# ───────────────────────────────────────────────────────────────

def get_migration_status(conn: sqlite3.Connection) -> Dict[str, object]:
    """Return current version and pending list (no changes applied)."""
    current = _get_current_version(conn)
    pending = [v for v in get_registered_versions() if v > current]
    return {
        "current_version": current,
        "latest_version": get_latest_version(),
        "pending": pending,
        "configured_version": cfg.DB_SCHEMA_VERSION,
}
