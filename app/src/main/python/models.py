# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Domain Models
# ═══════════════════════════════════════════════════════════════
#  Plain dataclasses. No ORM. No magic.
#  Every model has:
#    - to_dict()      → for JSON serialization
#    - from_dict()    → for deserialization with validation hooks
#    - validate()     → raises ValueError if invalid
# ═══════════════════════════════════════════════════════════════

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
import time
import uuid


# ───────────────────────────────────────────────────────────────
#  Constants for enums (kept as strings for JSON safety)
# ───────────────────────────────────────────────────────────────

class LocationSource:
    GPS = "gps"
    NETWORK = "network"
    PASSIVE = "passive"
    CELL = "cell"
    WIFI = "wifi"
    UNKNOWN = "unknown"
    ALL = {GPS, NETWORK, PASSIVE, CELL, WIFI, UNKNOWN}


class CommandStatus:
    CREATED = "created"
    QUEUED = "queued"
    SENT = "sent"
    RECEIVED = "received"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ALL = {
        CREATED, QUEUED, SENT, RECEIVED, EXECUTING,
        SUCCESS, FAILED, EXPIRED, CANCELLED,
    }
    TERMINAL = {SUCCESS, FAILED, EXPIRED, CANCELLED}


class AlertType:
    GPS_OFF = "gps_off"
    GPS_ON = "gps_on"
    LOW_BATTERY = "low_battery"
    OFFLINE = "offline"
    HIGH_SPEED = "high_speed"
    GEOFENCE_ENTER = "geofence_enter"
    GEOFENCE_EXIT = "geofence_exit"
    ROUTE_DEVIATION = "route_deviation"
    SOS = "sos"
    UNUSUAL = "unusual"
    ALL = {
        GPS_OFF, GPS_ON, LOW_BATTERY, OFFLINE, HIGH_SPEED,
        GEOFENCE_ENTER, GEOFENCE_EXIT, ROUTE_DEVIATION, SOS, UNUSUAL,
    }


class AlertSeverity:
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    ALL = {INFO, WARNING, CRITICAL}


# ───────────────────────────────────────────────────────────────
#  Device
# ───────────────────────────────────────────────────────────────

@dataclass
class Device:
    device_id: str
    name: Optional[str] = None
    model: Optional[str] = None
    android_version: Optional[str] = None
    device_key: Optional[str] = None
    battery: Optional[int] = None
    is_charging: Optional[int] = None
    network: Optional[str] = None
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None
    last_seen: Optional[int] = None
    registered_at: Optional[int] = None
    active: int = 1

    def validate(self) -> None:
        if not self.device_id or len(self.device_id) > 128:
            raise ValueError("device_id required (1..128 chars)")
        if self.battery is not None and not (0 <= self.battery <= 100):
            raise ValueError("battery must be 0..100")
        if self.last_lat is not None and not (-90 <= self.last_lat <= 90):
            raise ValueError("last_lat out of range")
        if self.last_lon is not None and not (-180 <= self.last_lon <= 180):
            raise ValueError("last_lon out of range")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Device":
        m = cls(
            device_id=d.get("device_id", ""),
            name=d.get("name"),
            model=d.get("model"),
            android_version=d.get("android_version"),
            device_key=d.get("device_key"),
            battery=d.get("battery"),
            is_charging=d.get("is_charging"),
            network=d.get("network"),
            last_lat=d.get("last_lat"),
            last_lon=d.get("last_lon"),
            last_seen=d.get("last_seen"),
            registered_at=d.get("registered_at"),
            active=d.get("active", 1),
        )
        m.validate()
        return m


# ───────────────────────────────────────────────────────────────
#  Location
# ───────────────────────────────────────────────────────────────

@dataclass
class LocationPoint:
    event_id: str
    device_id: str
    lat: float
    lon: float
    timestamp: int
    accuracy: Optional[float] = None
    altitude: Optional[float] = None
    speed: Optional[float] = None
    bearing: Optional[float] = None
    source: str = LocationSource.UNKNOWN
    battery: Optional[int] = None
    synced: int = 1

    def validate(self) -> None:
        if not self.event_id:
            raise ValueError("event_id required")
        if not self.device_id:
            raise ValueError("device_id required")
        if not (-90 <= self.lat <= 90):
            raise ValueError(f"lat out of range: {self.lat}")
        if not (-180 <= self.lon <= 180):
            raise ValueError(f"lon out of range: {self.lon}")
        if self.timestamp <= 0:
            raise ValueError("timestamp must be positive")
        if self.accuracy is not None and self.accuracy < 0:
            raise ValueError("accuracy must be >= 0")
        if self.speed is not None and self.speed < 0:
            raise ValueError("speed must be >= 0")
        if self.source not in LocationSource.ALL:
            raise ValueError(f"unknown source: {self.source}")
        if self.battery is not None and not (0 <= self.battery <= 100):
            raise ValueError("battery must be 0..100")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LocationPoint":
        m = cls(
            event_id=d.get("event_id", ""),
            device_id=d.get("device_id", ""),
            lat=float(d.get("lat", 0.0)),
            lon=float(d.get("lon", 0.0)),
            timestamp=int(d.get("timestamp", 0)),
            accuracy=d.get("accuracy"),
            altitude=d.get("altitude"),
            speed=d.get("speed"),
            bearing=d.get("bearing"),
            source=d.get("source", LocationSource.UNKNOWN),
            battery=d.get("battery"),
            synced=d.get("synced", 1),
        )
        m.validate()
        return m

    def is_precise(self, threshold_m: float = 50.0) -> bool:
        return self.accuracy is not None and self.accuracy <= threshold_m

    def is_approximate(self, threshold_m: float = 500.0) -> bool:
        return self.accuracy is not None and self.accuracy <= threshold_m


# ───────────────────────────────────────────────────────────────
#  Stop
# ───────────────────────────────────────────────────────────────

@dataclass
class Stop:
    device_id: str
    lat: float
    lon: float
    start_time: int
    end_time: int
    duration_seconds: int
    radius: float = 50.0
    address: Optional[str] = None

    def validate(self) -> None:
        if not self.device_id:
            raise ValueError("device_id required")
        if self.duration_seconds < 0:
            raise ValueError("duration must be >= 0")
        if self.end_time < self.start_time:
            raise ValueError("end_time before start_time")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ───────────────────────────────────────────────────────────────
#  Command
# ───────────────────────────────────────────────────────────────

@dataclass
class Command:
    device_id: str
    command: str
    command_id: Optional[str] = None
    payload: Optional[str] = None
    status: str = CommandStatus.CREATED
    created_at: Optional[int] = None
    expires_at: Optional[int] = None
    executed_at: Optional[int] = None

    def validate(self) -> None:
        if not self.device_id:
            raise ValueError("device_id required")
        if not self.command or len(self.command) > 64:
            raise ValueError("command required (1..64 chars)")
        if self.status not in CommandStatus.ALL:
            raise ValueError(f"unknown status: {self.status}")

    def is_expired(self, now_ms: Optional[int] = None) -> bool:
        if self.expires_at is None:
            return False
        now_ms = now_ms or int(time.time() * 1000)
        return now_ms > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ───────────────────────────────────────────────────────────────
#  Geofence
# ───────────────────────────────────────────────────────────────

@dataclass
class Geofence:
    device_id: str
    name: str
    lat: float
    lon: float
    radius: int = 200
    active: int = 1

    def validate(self) -> None:
        if not self.device_id:
            raise ValueError("device_id required")
        if not self.name or len(self.name) > 64:
            raise ValueError("name required (1..64 chars)")
        if not (-90 <= self.lat <= 90):
            raise ValueError("lat out of range")
        if not (-180 <= self.lon <= 180):
            raise ValueError("lon out of range")
        if not (10 <= self.radius <= 100000):
            raise ValueError("radius must be 10..100000 meters")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ───────────────────────────────────────────────────────────────
#  Alert
# ───────────────────────────────────────────────────────────────

@dataclass
class Alert:
    device_id: str
    alert_type: str
    message: str
    severity: str = AlertSeverity.INFO
    created_at: Optional[int] = None
    delivered: int = 0

    def validate(self) -> None:
        if not self.device_id:
            raise ValueError("device_id required")
        if self.alert_type not in AlertType.ALL:
            raise ValueError(f"unknown alert_type: {self.alert_type}")
        if self.severity not in AlertSeverity.ALL:
            raise ValueError(f"severity must be one of {AlertSeverity.ALL}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ───────────────────────────────────────────────────────────────
#  Helper factories
# ───────────────────────────────────────────────────────────────

def new_event_id() -> str:
    """Generate a unique event id."""
    return str(uuid.uuid4())


def new_command_id() -> str:
    """Generate a unique command id."""
    return str(uuid.uuid4())
