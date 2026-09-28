# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Real-time Event Bus
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Deliver live updates to connected clients via long-polling.
#
#  Why not WebSocket?
#    Real WebSocket needs flask-sock/flask-socketio, which are
#    not installed in Chaquopy yet. Long-polling provides the same
#    functional outcome ("live" updates) using only Flask.
#    True WebSocket upgrade is planned for Phase 15.
#
#  Model:
#    - Single global EventBus (in-memory)
#    - Subscribers register interest in a device_id (or "*" for all)
#    - Each subscriber owns a thread-safe queue.Queue
#    - When publish() is called, events are pushed to matching queues
#    - HTTP endpoint blocks on queue.get(timeout) → returns events
#
#  Contract with HTTP layer (see server.py):
#    GET /api/v1/devices/<device_id>/watch?since=<ts>&timeout=<sec>
#    GET /api/v1/watch/all?since=<ts>&timeout=<sec>
#
#  Bounded behavior:
#    - Max subscriber queue size to prevent memory blowup
#    - Default timeout = 25s (well under typical proxy limits)
#    - Subscribers auto-cleaned on disconnect
# ═══════════════════════════════════════════════════════════════

import time
import queue
import threading
from typing import Optional, Dict, Any, List, Iterator, Callable


# ───────────────────────────────────────────────────────────────
#  Constants
# ───────────────────────────────────────────────────────────────

MAX_QUEUE_SIZE = 500         # events per subscriber
DEFAULT_TIMEOUT = 25.0       # seconds
MAX_TIMEOUT = 60.0           # cap


# ───────────────────────────────────────────────────────────────
#  Event
# ───────────────────────────────────────────────────────────────

class Event:
    __slots__ = ("type", "device_id", "data", "timestamp")

    def __init__(self, type_: str, device_id: str,
                 data: Optional[Dict[str, Any]] = None,
                 timestamp: Optional[int] = None):
        self.type = type_
        self.device_id = device_id
        self.data = data or {}
        self.timestamp = timestamp if timestamp is not None \
            else int(time.time() * 1000)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "device_id": self.device_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ───────────────────────────────────────────────────────────────
#  Subscriber
# ───────────────────────────────────────────────────────────────

class Subscriber:
    __slots__ = ("device_id", "queue", "created_at", "id")

    _next_id = 0
    _id_lock = threading.Lock()

    def __init__(self, device_id: str):
        with Subscriber._id_lock:
            Subscriber._next_id += 1
            self.id = Subscriber._next_id

        self.device_id = device_id
        self.queue: "queue.Queue[Event]" = queue.Queue(maxsize=MAX_QUEUE_SIZE)
        self.created_at = time.time()

    def try_push(self, event: Event) -> None:
        """Non-blocking push. Drops oldest if full."""
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            # Drop oldest then retry once
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except Exception:
                pass


# ───────────────────────────────────────────────────────────────
#  Event Bus
# ───────────────────────────────────────────────────────────────

class EventBus:

    def __init__(self):
        self._subs: Dict[int, Subscriber] = {}
        self._lock = threading.Lock()
        self._pub_count = 0
        self._sub_count = 0

    # ─── Subscribers ───

    def subscribe(self, device_id: str) -> Subscriber:
        sub = Subscriber(device_id)
        with self._lock:
            self._subs[sub.id] = sub
            self._sub_count += 1
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.pop(sub.id, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    # ─── Publish ───

    def publish(self, event: Event) -> int:
        """Push event to matching subscribers. Returns count delivered."""
        with self._lock:
            subs = list(self._subs.values())
            self._pub_count += 1

        delivered = 0
        for s in subs:
            # "*" subscribes to everything
            if s.device_id == "*" or s.device_id == event.device_id:
                s.try_push(event)
                delivered += 1
        return delivered

    def publish_simple(self, type_: str, device_id: str,
                       data: Optional[Dict[str, Any]] = None) -> int:
        return self.publish(Event(type_, device_id, data))

    # ─── Stats ───

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "subscribers": len(self._subs),
                "published_total": self._pub_count,
                "subscribed_total": self._sub_count,
            }


# ───────────────────────────────────────────────────────────────
#  Global singleton
# ───────────────────────────────────────────────────────────────

_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_bus() -> EventBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = EventBus()
        return _bus


# ───────────────────────────────────────────────────────────────
#  High-level publish helpers (called from server.py)
# ───────────────────────────────────────────────────────────────

def publish_location(device_id: str, location: Dict[str, Any]) -> int:
    """Called whenever a new location is stored."""
    return get_bus().publish_simple("location", device_id, location)


def publish_command_update(device_id: str,
                           command_id: str,
                           status: str,
                           result: Optional[str] = None) -> int:
    return get_bus().publish_simple("command_update", device_id, {
        "command_id": command_id,
        "status": status,
        "result": result,
    })


def publish_device_state(device_id: str, state: Dict[str, Any]) -> int:
    return get_bus().publish_simple("device_state", device_id, state)


def publish_alert(device_id: str, alert: Dict[str, Any]) -> int:
    return get_bus().publish_simple("alert", device_id, alert)


# ───────────────────────────────────────────────────────────────
#  HTTP long-poll handler
# ───────────────────────────────────────────────────────────────

def stream_events(device_id: str,
                  since: Optional[int] = None,
                  timeout: float = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """
    Block for up to `timeout` seconds, collecting events matching
    device_id. Returns a list of event dicts, possibly empty.

    `since` (ms) filters events older than `since`.
    Caller (Flask route) sends the result as JSON.
    """
    if timeout > MAX_TIMEOUT:
        timeout = MAX_TIMEOUT
    if timeout <= 0:
        timeout = 0.5

    bus = get_bus()
    sub = bus.subscribe(device_id)
    events: List[Dict[str, Any]] = []
    deadline = time.time() + timeout
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                ev = sub.queue.get(timeout=remaining)
            except queue.Empty:
                break
            if since is not None and ev.timestamp < since:
                continue
            events.append(ev.to_dict())
            # Drain quickly, then return
            if len(events) >= 50:
                break
            # keep collecting if more arrive immediately
            if sub.queue.empty():
                break
    finally:
        bus.unsubscribe(sub)
    return events


def watch_all_stream(since: Optional[int] = None,
                     timeout: float = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Same as stream_events but subscribes to '*'."""
    return stream_events("*", since=since, timeout=timeout)


# ───────────────────────────────────────────────────────────────
#  Diagnostics
# ───────────────────────────────────────────────────────────────

def stats() -> Dict[str, Any]:
    return get_bus().stats()
