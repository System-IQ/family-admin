# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Smart Tunnel Manager
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Manage one or more public tunnels (Cloudflare, ngrok, custom)
#    with automatic failover, health monitoring, and self-healing.
#
#  Architecture:
#    Python side (this file):
#      - State machine
#      - Watchdog thread
#      - URL change detection
#      - Persistence
#      - Callbacks to Kotlin / server / clients
#
#    Kotlin side (TunnelService.kt):
#      - Actually spawns cloudflared / ngrok binary
#      - Returns URL from stdout
#      - Reports health
#
#  Provider model:
#    A "provider" is a logical name (e.g. "cloudflare", "ngrok").
#    Each provider has its own Kotlin bridge callables.
#    The manager picks the active provider and can fail over.
#
#  Contract with Kotlin:
#    For each provider, Kotlin calls:
#        tunnel_manager.register_provider(
#            name       = "cloudflare",
#            start_cb   = <callable() -> str>,      # returns URL or ""
#            stop_cb    = <callable() -> bool>,
#            status_cb  = <callable() -> dict>,     # {"running": bool, "url": str}
#            health_cb  = <callable() -> bool>,     # optional
#        )
# ═══════════════════════════════════════════════════════════════

import time
import threading
from typing import Optional, Dict, Any, Callable, List

import database as db


# ───────────────────────────────────────────────────────────────
#  Constants
# ───────────────────────────────────────────────────────────────

SETTING_TUNNEL_URL = "tunnel.public_url"
SETTING_TUNNEL_PROVIDER = "tunnel.provider"
SETTING_TUNNEL_STARTED_AT = "tunnel.started_at"
SETTING_TUNNEL_HISTORY = "tunnel.history"

WATCHDOG_INTERVAL = 30             # seconds between health checks
HEALTH_TIMEOUT = 15                # seconds max per health probe
RESTART_BACKOFF_BASE = 5           # seconds
RESTART_BACKOFF_MAX = 300          # 5 minutes cap
CIRCUIT_BREAKER_THRESHOLD = 5      # consecutive failures
CIRCUIT_BREAKER_COOLDOWN = 600     # 10 minutes
HISTORY_MAX = 20


# ───────────────────────────────────────────────────────────────
#  Logging
# ───────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [TUNNEL] {msg}", flush=True)


# ───────────────────────────────────────────────────────────────
#  Provider
# ───────────────────────────────────────────────────────────────

class Provider:
    """A single tunnel provider backed by Kotlin callables."""

    __slots__ = (
        "name", "start_cb", "stop_cb", "status_cb", "health_cb",
        "priority", "enabled",
    )

    def __init__(
        self,
        name: str,
        start_cb: Callable[[], str],
        stop_cb: Callable[[], bool],
        status_cb: Optional[Callable[[], Dict[str, Any]]] = None,
        health_cb: Optional[Callable[[], bool]] = None,
        priority: int = 50,
    ):
        self.name = name
        self.start_cb = start_cb
        self.stop_cb = stop_cb
        self.status_cb = status_cb
        self.health_cb = health_cb
        self.priority = priority
        self.enabled = True

    def start(self) -> str:
        try:
            url = self.start_cb() or ""
            return url.strip()
        except Exception as e:
            _log(f"❌ Provider {self.name} start raised: {e}")
            return ""

    def stop(self) -> bool:
        try:
            return bool(self.stop_cb())
        except Exception as e:
            _log(f"⚠️ Provider {self.name} stop raised: {e}")
            return False

    def status(self) -> Dict[str, Any]:
        if not self.status_cb:
            return {}
        try:
            return dict(self.status_cb() or {})
        except Exception:
            return {}

    def health(self) -> bool:
        if not self.health_cb:
            # No health callback → assume OK
            return True
        try:
            return bool(self.health_cb())
        except Exception:
            return False


# ───────────────────────────────────────────────────────────────
#  Manager state
# ───────────────────────────────────────────────────────────────

class TunnelManager:
    def __init__(self):
        self._providers: Dict[str, Provider] = {}
        self._active_provider: Optional[str] = None
        self._current_url: str = ""
        self._running: bool = False
        self._started_at: int = 0
        self._lock = threading.RLock()

        # Health/watchdog
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self._last_health_at: int = 0
        self._last_health_ok: bool = False
        self._consecutive_failures: int = 0
        self._circuit_open_until: int = 0
        self._next_retry_at: int = 0

        # Callbacks
        self._on_url_change: Optional[Callable[[str, str], None]] = None
        self._on_health_change: Optional[Callable[[bool], None]] = None

        # Metrics
        self._metrics = {
            "starts_total": 0,
            "stops_total": 0,
            "restarts_total": 0,
            "failures_total": 0,
            "url_changes_total": 0,
            "health_checks_total": 0,
            "health_failures_total": 0,
        }

        # History: [{url, provider, at}, ...]
        self._history: List[Dict[str, Any]] = []
        self._load_persisted()

    # ─────────────────────────────────────────────────────────
    #  Persistence
    # ─────────────────────────────────────────────────────────

    def _load_persisted(self) -> None:
        try:
            url = self._setting_get(SETTING_TUNNEL_URL)
            prov = self._setting_get(SETTING_TUNNEL_PROVIDER)
            if url:
                self._current_url = url
            if prov:
                self._active_provider = prov
            hist_raw = self._setting_get(SETTING_TUNNEL_HISTORY)
            if hist_raw:
                import json
                self._history = json.loads(hist_raw)[:HISTORY_MAX]
        except Exception:
            pass

    def _setting_get(self, key: str) -> Optional[str]:
        try:
            conn = db._get_conn()
            cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else None
        except Exception:
            return None

    def _setting_set(self, key: str, value: str) -> None:
        try:
            with db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, value, int(time.time() * 1000)),
                )
        except Exception as e:
            _log(f"⚠️ setting_set failed: {e}")

    def _persist_current(self) -> None:
        self._setting_set(SETTING_TUNNEL_URL, self._current_url or "")
        self._setting_set(SETTING_TUNNEL_PROVIDER, self._active_provider or "")
        self._setting_set(SETTING_TUNNEL_STARTED_AT, str(self._started_at or 0))
        import json
        self._setting_set(SETTING_TUNNEL_HISTORY,
                          json.dumps(self._history[-HISTORY_MAX:]))

    # ─────────────────────────────────────────────────────────
    #  Provider registration
    # ─────────────────────────────────────────────────────────

    def register_provider(
        self,
        name: str,
        start_cb: Callable[[], str],
        stop_cb: Callable[[], bool],
        status_cb: Optional[Callable[[], Dict[str, Any]]] = None,
        health_cb: Optional[Callable[[], bool]] = None,
        priority: int = 50,
    ) -> None:
        with self._lock:
            self._providers[name] = Provider(
                name=name,
                start_cb=start_cb,
                stop_cb=stop_cb,
                status_cb=status_cb,
                health_cb=health_cb,
                priority=priority,
            )
        _log(f"✅ Provider registered: {name} (priority={priority})")

    def unregister_provider(self, name: str) -> None:
        with self._lock:
            self._providers.pop(name, None)
        _log(f"Provider removed: {name}")

    def list_providers(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {"name": p.name, "priority": p.priority, "enabled": p.enabled}
                for p in sorted(self._providers.values(), key=lambda x: x.priority)
            ]

    def _sorted_providers(self) -> List[Provider]:
        with self._lock:
            return sorted(
                [p for p in self._providers.values() if p.enabled],
                key=lambda x: x.priority,
            )

    # ─────────────────────────────────────────────────────────
    #  Callbacks
    # ─────────────────────────────────────────────────────────

    def on_url_change(self, cb: Callable[[str, str], None]) -> None:
        with self._lock:
            self._on_url_change = cb

    def on_health_change(self, cb: Callable[[bool], None]) -> None:
        with self._lock:
            self._on_health_change = cb

    def _emit_url_change(self, old: str, new: str) -> None:
        with self._lock:
            cb = self._on_url_change
        if cb and old != new:
            try:
                cb(old, new)
            except Exception as e:
                _log(f"⚠️ on_url_change callback raised: {e}")

    def _emit_health_change(self, ok: bool) -> None:
        with self._lock:
            cb = self._on_health_change
        if cb:
            try:
                cb(ok)
            except Exception as e:
                _log(f"⚠️ on_health_change callback raised: {e}")

    # ─────────────────────────────────────────────────────────
    #  Start / Stop
    # ─────────────────────────────────────────────────────────

    def start(self, prefer: Optional[str] = None) -> Dict[str, Any]:
        """
        Start tunnel using preferred provider or the highest-priority one.
        On failure, tries the next provider.
        """
        with self._lock:
            if self._running and self._current_url:
                return self._snapshot()

            if self._is_circuit_open():
                return {
                    **self._snapshot(),
                    "error": "circuit_open",
                    "retry_in_seconds": (self._circuit_open_until -
                                         int(time.time() * 1000)) // 1000,
                }

        providers = self._sorted_providers()
        if prefer:
            providers = sorted(
                providers,
                key=lambda p: (0 if p.name == prefer else 1, p.priority),
            )

        if not providers:
            _log("❌ No providers registered")
            return {**self._snapshot(), "error": "no_providers"}

        last_error = None
        for p in providers:
            _log(f"🚀 Attempting provider: {p.name}")
            url = p.start()
            if url:
                with self._lock:
                    old_url = self._current_url
                    self._current_url = url
                    self._active_provider = p.name
                    self._running = True
                    self._started_at = int(time.time() * 1000)
                    self._consecutive_failures = 0
                    self._next_retry_at = 0
                    self._metrics["starts_total"] += 1
                    self._record_history(url, p.name)
                self._persist_current()
                _log(f"✅ Tunnel active: {url} via {p.name}")
                self._emit_url_change(old_url, url)
                self._start_watchdog()
                return self._snapshot()
            else:
                last_error = f"provider_{p.name}_failed"
                _log(f"⚠️ Provider {p.name} failed")

        with self._lock:
            self._consecutive_failures += 1
            self._metrics["failures_total"] += 1
            self._schedule_next_retry()

        return {**self._snapshot(), "error": last_error or "all_failed"}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            active = self._active_provider

        if active and active in self._providers:
            self._providers[active].stop()

        with self._lock:
            self._running = False
            self._current_url = ""
            self._metrics["stops_total"] += 1
        self._persist_current()
        self._stop_watchdog()

        _log("Tunnel stopped")
        return self._snapshot()

    def force_restart(self) -> Dict[str, Any]:
        """Stop current tunnel, then start fresh."""
        with self._lock:
            self._metrics["restarts_total"] += 1
        _log("🔄 Force restart requested")
        self.stop()
        time.sleep(1.0)
        return self.start()

    # ─────────────────────────────────────────────────────────
    #  Circuit breaker / retry scheduling
    # ─────────────────────────────────────────────────────────

    def _is_circuit_open(self) -> bool:
        return int(time.time() * 1000) < self._circuit_open_until

    def _schedule_next_retry(self) -> None:
        delay = min(
            RESTART_BACKOFF_BASE * (2 ** max(0, self._consecutive_failures - 1)),
            RESTART_BACKOFF_MAX,
        )
        self._next_retry_at = int(time.time() * 1000) + delay * 1000
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open_until = (
                int(time.time() * 1000) + CIRCUIT_BREAKER_COOLDOWN * 1000
            )
            _log(f"⛔ Circuit breaker open for {CIRCUIT_BREAKER_COOLDOWN}s")

    # ─────────────────────────────────────────────────────────
    #  Watchdog
    # ─────────────────────────────────────────────────────────

    def _start_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="TunnelWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()
        _log("🩺 Watchdog started")

    def _stop_watchdog(self) -> None:
        self._watchdog_stop.set()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.is_set():
            try:
                self._watchdog_tick()
            except Exception as e:
                _log(f"⚠️ Watchdog tick error: {e}")
            self._watchdog_stop.wait(WATCHDOG_INTERVAL)

    def _watchdog_tick(self) -> None:
        with self._lock:
            if not self._running:
                return
            active = self._active_provider
            provider = self._providers.get(active) if active else None

        if provider is None:
            return

        # Health check
        with self._lock:
            self._metrics["health_checks_total"] += 1
            self._last_health_at = int(time.time() * 1000)
        ok = provider.health()
        with self._lock:
            self._last_health_ok = ok
            if not ok:
                self._metrics["health_failures_total"] += 1
                self._consecutive_failures += 1
        self._emit_health_change(ok)

        if ok:
            # Also verify URL didn't change silently
            st = provider.status()
            new_url = (st.get("url") or "").strip()
            if new_url and new_url != self._current_url:
                with self._lock:
                    old = self._current_url
                    self._current_url = new_url
                    self._metrics["url_changes_total"] += 1
                    self._record_history(new_url, provider.name)
                self._persist_current()
                self._emit_url_change(old, new_url)
                _log(f"🔀 URL changed: {old} → {new_url}")
        else:
            _log("❌ Watchdog health check failed — restarting tunnel")
            self._schedule_next_retry()
            self.force_restart()

    # ─────────────────────────────────────────────────────────
    #  History
    # ─────────────────────────────────────────────────────────

    def _record_history(self, url: str, provider: str) -> None:
        entry = {
            "url": url,
            "provider": provider,
            "at": int(time.time() * 1000),
        }
        self._history.append(entry)
        self._history = self._history[-HISTORY_MAX:]

    def history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._history)

    # ─────────────────────────────────────────────────────────
    #  Public read API
    # ─────────────────────────────────────────────────────────

    def get_url(self) -> str:
        with self._lock:
            if self._current_url:
                return self._current_url
        return self._setting_get(SETTING_TUNNEL_URL) or ""

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "url": self._current_url,
                "provider": self._active_provider,
                "started_at": self._started_at,
                "consecutive_failures": self._consecutive_failures,
                "last_health_at": self._last_health_at,
                "last_health_ok": self._last_health_ok,
                "next_retry_at": self._next_retry_at,
                "circuit_open": self._is_circuit_open(),
                "providers": self.list_providers(),
                "metrics": dict(self._metrics),
            }

    def status(self) -> Dict[str, Any]:
        return self._snapshot()

    def reset(self) -> None:
        with self._lock:
            self._running = False
            self._current_url = ""
            self._active_provider = None
            self._started_at = 0
            self._consecutive_failures = 0
            self._circuit_open_until = 0
            self._next_retry_at = 0
            self._history = []


# ───────────────────────────────────────────────────────────────
#  Singleton
# ───────────────────────────────────────────────────────────────

_manager: Optional[TunnelManager] = None
_manager_lock = threading.Lock()


def get_manager() -> TunnelManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = TunnelManager()
        return _manager


# ───────────────────────────────────────────────────────────────
#  Module-level API (Kotlin-facing, same as v1 for compatibility)
# ───────────────────────────────────────────────────────────────

def register_provider(name: str,
                     start_cb: Callable[[], str],
                     stop_cb: Callable[[], bool],
                     status_cb: Optional[Callable[[], Dict[str, Any]]] = None,
                     health_cb: Optional[Callable[[], bool]] = None,
                     priority: int = 50) -> None:
    get_manager().register_provider(name, start_cb, stop_cb, status_cb, health_cb, priority)


def unregister_provider(name: str) -> None:
    get_manager().unregister_provider(name)


def unregister_all() -> None:
    m = get_manager()
    for p in m.list_providers():
        m.unregister_provider(p["name"])


# Backwards-compatible alias used by earlier step
def register_tunnel_bridge(start_cb, stop_cb, status_cb=None):
    register_provider(
        name="cloudflare",
        start_cb=start_cb,
        stop_cb=stop_cb,
        status_cb=status_cb,
        priority=10,
    )


def unregister_tunnel_bridge():
    unregister_all()


def is_bridge_ready() -> bool:
    return len(get_manager().list_providers()) > 0


def start_tunnel(provider: str = "cloudflare") -> Dict[str, Any]:
    return get_manager().start(prefer=provider)


def stop_tunnel() -> Dict[str, Any]:
    return get_manager().stop()


def force_restart() -> Dict[str, Any]:
    return get_manager().force_restart()


def get_tunnel_url() -> str:
    return get_manager().get_url()


def is_running() -> bool:
    return get_manager().is_running()


def get_status() -> Dict[str, Any]:
    return get_manager().status()


def history() -> List[Dict[str, Any]]:
    return get_manager().history()


def on_url_change(cb: Callable[[str, str], None]) -> None:
    get_manager().on_url_change(cb)


def on_health_change(cb: Callable[[bool], None]) -> None:
    get_manager().on_health_change(cb)


# ───────────────────────────────────────────────────────────────
#  HTTP-facing helpers (used by server.py)
# ───────────────────────────────────────────────────────────────

def http_status() -> Dict[str, Any]:
    m = get_manager()
    s = m.status()
    return {
        "running": s["running"],
        "url": s["url"],
        "provider": s["provider"],
        "started_at": s["started_at"],
        "bridge_ready": is_bridge_ready(),
        "last_health_ok": s["last_health_ok"],
        "consecutive_failures": s["consecutive_failures"],
        "circuit_open": s["circuit_open"],
        "providers": s["providers"],
    }


def http_start() -> Dict[str, Any]:
    return start_tunnel()


def http_stop() -> Dict[str, Any]:
    return stop_tunnel()
