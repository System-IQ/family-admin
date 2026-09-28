# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Dispatch Queue Manager
# ═══════════════════════════════════════════════════════════════

import time
import queue
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List


DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_BACKOFF_MAX = 300.0
WORKER_POLL_TIMEOUT = 1.0


@dataclass(order=True)
class WorkItem:
    kind: str
    target: str
    payload: Dict[str, Any] = field(compare=False)
    priority: int = field(default=5, compare=True)
    attempts: int = field(default=0, compare=False)
    next_attempt_at: float = field(default=0.0, compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    last_error: Optional[str] = field(default=None, compare=False)
    item_id: Optional[str] = field(default=None, compare=False)


class QueueManager:

    def __init__(self,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 backoff_base: float = DEFAULT_BACKOFF_BASE,
                 backoff_max: float = DEFAULT_BACKOFF_MAX):
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

        self._command_q: "queue.PriorityQueue[WorkItem]" = queue.PriorityQueue()
        self._alert_q: "queue.PriorityQueue[WorkItem]" = queue.PriorityQueue()

        self._command_handlers: Dict[str, Callable[[WorkItem], bool]] = {}
        self._alert_handlers: Dict[str, Callable[[WorkItem], bool]] = {}

        self._running = False
        self._worker_threads: List[threading.Thread] = []
        self._lock = threading.Lock()

        self._stats: Dict[str, int] = {
            "commands_enqueued": 0,
            "commands_sent": 0,
            "commands_failed": 0,
            "alerts_enqueued": 0,
            "alerts_sent": 0,
            "alerts_failed": 0,
        }

    def register_command_handler(self, name: str,
                                 handler: Callable[[WorkItem], bool]) -> None:
        with self._lock:
            self._command_handlers[name] = handler

    def register_alert_handler(self, name: str,
                               handler: Callable[[WorkItem], bool]) -> None:
        with self._lock:
            self._alert_handlers[name] = handler

    def unregister_handlers(self) -> None:
        with self._lock:
            self._command_handlers.clear()
            self._alert_handlers.clear()

    def enqueue_command(self, device_id: str, command_id: str,
                        command: str, payload: Optional[str] = None,
                        priority: int = 5) -> None:
        item = WorkItem(
            kind="command",
            target=device_id,
            priority=priority,
            payload={
                "command_id": command_id,
                "command": command,
                "payload": payload,
            },
            item_id=command_id,
        )
        self._command_q.put(item)
        with self._lock:
            self._stats["commands_enqueued"] += 1

    def enqueue_alert(self, device_id: str, alert: Dict[str, Any],
                      priority: int = 3) -> None:
        item = WorkItem(
            kind="alert",
            target=device_id,
            priority=priority,
            payload=dict(alert),
            item_id=alert.get("id"),
        )
        self._alert_q.put(item)
        with self._lock:
            self._stats["alerts_enqueued"] += 1

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        cmd_thread = threading.Thread(
            target=self._worker_loop,
            args=("command", self._command_q, self._command_handlers,
                  "commands_sent", "commands_failed"),
            name="QueueMgr-Command",
            daemon=True,
        )
        alert_thread = threading.Thread(
            target=self._worker_loop,
            args=("alert", self._alert_q, self._alert_handlers,
                  "alerts_sent", "alerts_failed"),
            name="QueueMgr-Alert",
            daemon=True,
        )
        self._worker_threads = [cmd_thread, alert_thread]
        for t in self._worker_threads:
            t.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._running = False
        for t in self._worker_threads:
            t.join(timeout=timeout)

    def _worker_loop(self,
                     kind: str,
                     q: "queue.PriorityQueue[WorkItem]",
                     handlers: Dict[str, Callable[[WorkItem], bool]],
                     sent_key: str,
                     failed_key: str) -> None:

        while self._running:
            try:
                item = q.get(timeout=WORKER_POLL_TIMEOUT)
            except queue.Empty:
                continue

            if item.attempts >= self._max_attempts:
                with self._lock:
                    self._stats[failed_key] += 1
                self._log(f"❌ Dropped {kind} for {item.target} after "
                          f"{item.attempts} attempts: {item.last_error}")
                q.task_done()
                continue

            now = time.time()
            if item.next_attempt_at > now:
                q.put(item)
                time.sleep(min(item.next_attempt_at - now, 1.0))
                continue

            handler = None
            with self._lock:
                for name, h in handlers.items():
                    handler = h
                    break

            if handler is None:
                self._log(f"⚠️ No handler for {kind} → {item.target} (dropped)")
                with self._lock:
                    self._stats[failed_key] += 1
                q.task_done()
                continue

            try:
                ok = bool(handler(item))
            except Exception as e:
                ok = False
                item.last_error = str(e)

            if ok:
                with self._lock:
                    self._stats[sent_key] += 1
                self._log(f"✅ Sent {kind} → {item.target}")
                q.task_done()
            else:
                item.attempts += 1
                delay = min(self._backoff_base ** item.attempts,
                            self._backoff_max)
                item.next_attempt_at = time.time() + delay
                q.put(item)
                self._log(f"🔁 Retry {kind} → {item.target} in {delay:.1f}s "
                          f"(attempt {item.attempts}/{self._max_attempts})")
                q.task_done()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            s = dict(self._stats)
        s["command_queue_size"] = self._command_q.qsize()
        s["alert_queue_size"] = self._alert_q.qsize()
        s["running"] = self._running
        s["command_handlers"] = list(self._command_handlers.keys())
        s["alert_handlers"] = list(self._alert_handlers.keys())
        return s

    @staticmethod
    def _log(msg: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [QUEUE] {msg}", flush=True)


_manager: Optional[QueueManager] = None
_manager_lock = threading.Lock()


def get_manager() -> QueueManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = QueueManager()
        return _manager


def start() -> None:
    get_manager().start()


def stop() -> None:
    if _manager is not None:
        _manager.stop()


def stats() -> Dict[str, Any]:
    return get_manager().stats()


# Kotlin-facing wrappers
def enqueue_command(device_id: str, command_id: str,
                    command: str, payload: Optional[str] = None,
                    priority: int = 5) -> None:
    get_manager().enqueue_command(
        device_id, command_id, command, payload, priority=priority
    )


def enqueue_alert(device_id: str, alert: Dict[str, Any],
                  priority: int = 3) -> None:
    get_manager().enqueue_alert(device_id, alert, priority=priority)


def register_command_handler(name: str, handler: Callable) -> None:
    get_manager().register_command_handler(name, handler)


def register_alert_handler(name: str, handler: Callable) -> None:
    get_manager().register_alert_handler(name, handler)
