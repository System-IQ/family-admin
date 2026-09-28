# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Anomaly Detector
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Detect SPECIFIC, ACTIONABLE anomalies from location history:
#      • New place      — visited a location never seen before
#      • Route deviation — went far off the usual path
#      • Unexpected stop — long stop during usual-moving hours
#      • Missed routine  — didn't visit a habitual place today
#      • Off-hours activity — active during usual-sleep window
#
#  Every anomaly carries:
#      - score (0..1)
#      - level (info|warning|critical)
#      - reasons (explanation)
#      - suggested_alert (matches AlertType in models.py)
#
#  This module is DISTINCT from ai_analyzer.detect_anomaly():
#      ai_analyzer → single aggregate score
#      anomaly_detector → list of specific, labeled anomalies
# ═══════════════════════════════════════════════════════════════

import math
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

import config as cfg
import database as db
from models import AlertType


# ───────────────────────────────────────────────────────────────
#  Constants
# ───────────────────────────────────────────────────────────────

MIN_POINTS = 30
BASELINE_DAYS = 14          # how far back to learn "normal"
RECENT_WINDOW_HOURS = 24    # how far forward to look for anomalies

# Clustering
PLACE_CLUSTER_RADIUS_M = 150.0
MIN_SAMPLES_FOR_PLACE = 3

# Thresholds
NEW_PLACE_MIN_STAY_S = 300           # 5 minutes at new place = anomaly
ROUTE_DEVIATION_MIN_M = 1500.0       # >1.5 km off usual path
UNEXPECTED_STOP_MIN_S = 1800         # 30 minutes where device usually moves
SLEEP_HOUR_START = 23
SLEEP_HOUR_END = 6
MISSED_ROUTINE_MIN_RATE = 0.7        # visited this place on >=70% of days


# ───────────────────────────────────────────────────────────────
#  Result types
# ───────────────────────────────────────────────────────────────

@dataclass
class Anomaly:
    kind: str                      # "new_place" | "route_deviation" | ...
    score: float                   # 0.0 .. 1.0
    level: str                     # "info" | "warning" | "critical"
    reasons: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    suggested_alert: str = AlertType.UNUSUAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "score": round(self.score, 3),
            "level": self.level,
            "reasons": self.reasons,
            "data": self.data,
            "suggested_alert": self.suggested_alert,
        }


@dataclass
class DetectionReport:
    available: bool = False
    reason: str = ""
    total_anomalies: int = 0
    highest_level: str = "normal"   # normal|info|warning|critical
    anomalies: List[Anomaly] = field(default_factory=list)
    generated_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "total_anomalies": self.total_anomalies,
            "highest_level": self.highest_level,
            "anomalies": [a.to_dict() for a in self.anomalies],
            "generated_at": self.generated_at or int(time.time() * 1000),
        }


# ───────────────────────────────────────────────────────────────
#  Helpers
# ───────────────────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = cfg.EARTH_RADIUS_M
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _hour_of_day(ts_ms: int) -> int:
    # Use LOCAL time (not UTC) so sleep-hours logic matches user's clock
    from datetime import datetime
    return datetime.fromtimestamp(ts_ms / 1000).hour


def _day_key(ts_ms: int) -> int:
    return ts_ms // (86400 * 1000)


def _load_points(device_id: str, since_ms: int) -> List[Dict[str, Any]]:
    pts = db.get_locations(device_id, since=since_ms, limit=50000)
    pts.sort(key=lambda p: p["timestamp"])
    return pts


def _cluster_places(points: List[Dict[str, Any]],
                    radius_m: float = PLACE_CLUSTER_RADIUS_M
                    ) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for p in points:
        placed = False
        for c in clusters:
            if _haversine(p["lat"], p["lon"], c["lat"], c["lon"]) <= radius_m:
                n = c["count"]
                c["lat"] = (c["lat"] * n + p["lat"]) / (n + 1)
                c["lon"] = (c["lon"] * n + p["lon"]) / (n + 1)
                c["count"] = n + 1
                c["timestamps"].append(p["timestamp"])
                placed = True
                break
        if not placed:
            clusters.append({
                "lat": p["lat"], "lon": p["lon"], "count": 1,
                "timestamps": [p["timestamp"]],
            })
    sig = [c for c in clusters if c["count"] >= MIN_SAMPLES_FOR_PLACE]
    sig.sort(key=lambda c: c["count"], reverse=True)
    for i, c in enumerate(sig):
        c["place_id"] = f"P{i+1}"
    return sig


def _nearest_place(lat: float, lon: float,
                   places: List[Dict[str, Any]],
                   max_distance_m: float = PLACE_CLUSTER_RADIUS_M
                   ) -> Optional[Dict[str, Any]]:
    best = None
    best_d = float("inf")
    for c in places:
        d = _haversine(lat, lon, c["lat"], c["lon"])
        if d < best_d and d <= max_distance_m:
            best = c
            best_d = d
    return best


# ───────────────────────────────────────────────────────────────
#  Detector 1: New place
# ───────────────────────────────────────────────────────────────

def _detect_new_place(recent: List[Dict[str, Any]],
                      baseline_places: List[Dict[str, Any]]
                      ) -> Optional[Anomaly]:
    if not recent:
        return None

    recent_places = _cluster_places(recent)
    if not recent_places:
        return None

    new_found: List[Dict[str, Any]] = []
    for rp in recent_places:
        # Is this cluster far from every baseline place?
        min_dist = float("inf")
        for bp in baseline_places:
            d = _haversine(rp["lat"], rp["lon"], bp["lat"], bp["lon"])
            if d < min_dist:
                min_dist = d
        if min_dist > PLACE_CLUSTER_RADIUS_M * 2:  # 300 m
            # how long did we stay?
            if rp["count"] > 0:
                times = sorted(rp["timestamps"])
                stay_s = (times[-1] - times[0]) // 1000
                if stay_s >= NEW_PLACE_MIN_STAY_S:
                    new_found.append({
                        "lat": round(rp["lat"], 6),
                        "lon": round(rp["lon"], 6),
                        "point_count": rp["count"],
                        "stay_seconds": stay_s,
                        "nearest_baseline_place_distance_m": round(min_dist, 1),
                    })

    if not new_found:
        return None

    # Score: stay duration drives urgency
    max_stay = max(p["stay_seconds"] for p in new_found)
    score = min(1.0, max_stay / 3600.0)  # 1h stay = 1.0
    level = "warning" if score < 0.6 else "critical"

    return Anomaly(
        kind="new_place",
        score=score,
        level=level,
        reasons=[
            f"Visited {len(new_found)} location(s) not in baseline",
            f"Longest stay: {max_stay // 60} minutes",
        ],
        data={"new_places": new_found},
        suggested_alert=AlertType.UNUSUAL,
    )


# ───────────────────────────────────────────────────────────────
#  Detector 2: Route deviation
# ───────────────────────────────────────────────────────────────

def _detect_route_deviation(recent: List[Dict[str, Any]],
                            baseline: List[Dict[str, Any]]
                            ) -> Optional[Anomaly]:
    if not recent or len(baseline) < MIN_POINTS:
        return None

    baseline_places = _cluster_places(baseline)
    if not baseline_places:
        return None

    max_deviation = 0.0
    worst_point = None

    for p in recent:
        # Min distance to any baseline place
        min_d = min(
            _haversine(p["lat"], p["lon"], bp["lat"], bp["lon"])
            for bp in baseline_places
        )
        if min_d > max_deviation:
            max_deviation = min_d
            worst_point = p

    if max_deviation < ROUTE_DEVIATION_MIN_M:
        return None

    score = min(1.0, (max_deviation - ROUTE_DEVIATION_MIN_M) / 5000.0)
    level = "warning" if score < 0.5 else "critical"

    return Anomaly(
        kind="route_deviation",
        score=score,
        level=level,
        reasons=[
            f"Farthest from usual places: {max_deviation / 1000:.2f} km",
            f"Threshold: {ROUTE_DEVIATION_MIN_M / 1000:.1f} km",
        ],
        data={
            "max_deviation_m": round(max_deviation, 1),
            "worst_point": {
                "lat": round(worst_point["lat"], 6) if worst_point else None,
                "lon": round(worst_point["lon"], 6) if worst_point else None,
                "timestamp": worst_point["timestamp"] if worst_point else None,
            } if worst_point else {},
        },
        suggested_alert=AlertType.ROUTE_DEVIATION,
    )


# ───────────────────────────────────────────────────────────────
#  Detector 3: Unexpected stop
# ───────────────────────────────────────────────────────────────

def _detect_unexpected_stop(recent: List[Dict[str, Any]],
                            baseline: List[Dict[str, Any]]
                            ) -> Optional[Anomaly]:
    if not recent or len(baseline) < MIN_POINTS:
        return None

    # Baseline: which hours is device typically moving?
    moving_hours: Dict[int, int] = defaultdict(int)
    total_hours: Dict[int, int] = defaultdict(int)

    for p in baseline:
        h = _hour_of_day(p["timestamp"])
        total_hours[h] += 1
        if (p.get("speed") or 0) > 1.0:
            moving_hours[h] += 1

    # Hours where device moves > 50% of the time
    usually_moving = set()
    for h, total in total_hours.items():
        if total >= 3 and moving_hours.get(h, 0) / total > 0.5:
            usually_moving.add(h)

    if not usually_moving:
        return None

    # Find recent long stops during those hours
    stops: List[Dict[str, Any]] = []
    cluster: List[Dict[str, Any]] = []

    for p in recent:
        spd = p.get("speed") or 0
        if spd < 0.5:
            cluster.append(p)
        else:
            if len(cluster) >= 2:
                dur = (cluster[-1]["timestamp"] - cluster[0]["timestamp"]) // 1000
                if dur >= UNEXPECTED_STOP_MIN_S:
                    h = _hour_of_day(cluster[0]["timestamp"])
                    if h in usually_moving:
                        stops.append({
                            "hour": h,
                            "duration_seconds": dur,
                            "lat": round(
                                sum(c["lat"] for c in cluster) / len(cluster), 6),
                            "lon": round(
                                sum(c["lon"] for c in cluster) / len(cluster), 6),
                            "start_time": cluster[0]["timestamp"],
                        })
            cluster = []

    if len(cluster) >= 2:
        dur = (cluster[-1]["timestamp"] - cluster[0]["timestamp"]) // 1000
        if dur >= UNEXPECTED_STOP_MIN_S:
            h = _hour_of_day(cluster[0]["timestamp"])
            if h in usually_moving:
                stops.append({
                    "hour": h,
                    "duration_seconds": dur,
                    "lat": round(sum(c["lat"] for c in cluster) / len(cluster), 6),
                    "lon": round(sum(c["lon"] for c in cluster) / len(cluster), 6),
                    "start_time": cluster[0]["timestamp"],
                })

    if not stops:
        return None

    max_stop = max(s["duration_seconds"] for s in stops)
    score = min(1.0, max_stop / 7200.0)  # 2 hours = 1.0
    level = "warning" if score < 0.5 else "critical"

    return Anomaly(
        kind="unexpected_stop",
        score=score,
        level=level,
        reasons=[
            f"Long stop during usually-moving hour(s): {sorted(s['hour'] for s in stops)}",
            f"Longest stop: {max_stop // 60} minutes",
        ],
        data={"stops": stops},
        suggested_alert=AlertType.UNUSUAL,
    )


# ───────────────────────────────────────────────────────────────
#  Detector 4: Missed routine
# ───────────────────────────────────────────────────────────────

def _detect_missed_routine(recent: List[Dict[str, Any]],
                           baseline: List[Dict[str, Any]]
                           ) -> Optional[Anomaly]:
    if not recent or len(baseline) < MIN_POINTS:
        return None

    baseline_places = _cluster_places(baseline)
    if not baseline_places:
        return None

    # Count per-day visits to each place in baseline
    baseline_days = set(_day_key(p["timestamp"]) for p in baseline)
    if len(baseline_days) < 5:
        return None

    place_day_visit: Dict[str, set] = defaultdict(set)
    for p in baseline:
        place = _nearest_place(p["lat"], p["lon"], baseline_places)
        if place:
            place_day_visit[place["place_id"]].add(_day_key(p["timestamp"]))

    # For each place, hit-rate = days visited / total days
    habitual_places: Dict[str, float] = {}
    for pid, days in place_day_visit.items():
        rate = len(days) / len(baseline_days)
        if rate >= MISSED_ROUTINE_MIN_RATE:
            habitual_places[pid] = rate

    if not habitual_places:
        return None

    # Today = recent day keys
    recent_days = set(_day_key(p["timestamp"]) for p in recent)
    recent_visits_by_place: Dict[str, set] = defaultdict(set)
    for p in recent:
        place = _nearest_place(p["lat"], p["lon"], baseline_places)
        if place:
            recent_visits_by_place[place["place_id"]].add(_day_key(p["timestamp"]))

    missed: List[Dict[str, Any]] = []
    for pid, rate in habitual_places.items():
        # if habitual place not visited in any recent day
        if not recent_visits_by_place.get(pid, set()) & recent_days:
            place = next((p for p in baseline_places if p["place_id"] == pid), None)
            if place:
                missed.append({
                    "place_id": pid,
                    "lat": round(place["lat"], 6),
                    "lon": round(place["lon"], 6),
                    "habitual_rate": round(rate, 2),
                })

    if not missed:
        return None

    score = min(1.0, len(missed) / 3.0)
    level = "info" if score < 0.4 else "warning"

    return Anomaly(
        kind="missed_routine",
        score=score,
        level=level,
        reasons=[
            f"Missed visit to {len(missed)} habitual place(s) today",
            f"Habit threshold: >= {MISSED_ROUTINE_MIN_RATE * 100:.0f}% of days",
        ],
        data={"missed_places": missed},
        suggested_alert=AlertType.UNUSUAL,
    )


# ───────────────────────────────────────────────────────────────
#  Detector 5: Off-hours activity
# ───────────────────────────────────────────────────────────────

def _detect_off_hours(recent: List[Dict[str, Any]],
                      baseline: List[Dict[str, Any]]
                      ) -> Optional[Anomaly]:
    if not recent or len(baseline) < MIN_POINTS:
        return None

    def is_sleep_hour(h: int) -> bool:
        return h >= SLEEP_HOUR_START or h < SLEEP_HOUR_END

    # Baseline: how often active during sleep hours?
    baseline_sleep_points = sum(
        1 for p in baseline if is_sleep_hour(_hour_of_day(p["timestamp"]))
    )
    baseline_sleep_rate = baseline_sleep_points / len(baseline)

    # Recent: same
    recent_sleep_points = [
        p for p in recent if is_sleep_hour(_hour_of_day(p["timestamp"]))
    ]
    recent_sleep_rate = len(recent_sleep_points) / len(recent) if recent else 0

    # Only flag if baseline rarely sleeps-active, but recent is very active
    if baseline_sleep_rate >= 0.10:
        return None  # device is naturally active at night
    if recent_sleep_rate <= 0.15:
        return None  # not enough night activity

    hours_seen = sorted(set(_hour_of_day(p["timestamp"]) for p in recent_sleep_points))

    score = min(1.0, recent_sleep_rate / 0.5)
    level = "warning" if score < 0.5 else "critical"

    return Anomaly(
        kind="off_hours_activity",
        score=score,
        level=level,
        reasons=[
            f"Active during sleep hours: {recent_sleep_rate * 100:.0f}% "
            f"(baseline {baseline_sleep_rate * 100:.0f}%)",
            f"Sleep hours: {SLEEP_HOUR_START}:00 - {SLEEP_HOUR_END}:00",
        ],
        data={
            "sleep_hours_seen": hours_seen,
            "recent_sleep_points": len(recent_sleep_points),
            "baseline_sleep_rate": round(baseline_sleep_rate, 3),
            "recent_sleep_rate": round(recent_sleep_rate, 3),
        },
        suggested_alert=AlertType.UNUSUAL,
    )


# ───────────────────────────────────────────────────────────────
#  Orchestration
# ───────────────────────────────────────────────────────────────

def detect_all(device_id: str,
               baseline_days: int = BASELINE_DAYS,
               recent_hours: int = RECENT_WINDOW_HOURS
               ) -> DetectionReport:
    now_ms = int(time.time() * 1000)
    baseline_since = now_ms - baseline_days * 86400 * 1000
    recent_since = now_ms - recent_hours * 3600 * 1000

    # Load points
    all_points = _load_points(device_id, baseline_since)
    if len(all_points) < MIN_POINTS:
        return DetectionReport(
            available=False,
            reason=f"insufficient_data (have {len(all_points)}, need {MIN_POINTS})",
        )

    baseline = [p for p in all_points if p["timestamp"] < recent_since]
    recent = [p for p in all_points if p["timestamp"] >= recent_since]

    if len(baseline) < MIN_POINTS:
        return DetectionReport(
            available=False,
            reason=f"insufficient_baseline (have {len(baseline)}, need {MIN_POINTS})",
        )

    baseline_places = _cluster_places(baseline)

    # Run all detectors
    findings: List[Anomaly] = []

    a = _detect_new_place(recent, baseline_places)
    if a:
        findings.append(a)

    a = _detect_route_deviation(recent, baseline)
    if a:
        findings.append(a)

    a = _detect_unexpected_stop(recent, baseline)
    if a:
        findings.append(a)

    a = _detect_missed_routine(recent, baseline)
    if a:
        findings.append(a)

    a = _detect_off_hours(recent, baseline)
    if a:
        findings.append(a)

    # Sort by score, then by level
    level_order = {"critical": 3, "warning": 2, "info": 1, "normal": 0}
    findings.sort(key=lambda x: (level_order[x.level], x.score), reverse=True)

    # Highest level
    if not findings:
        highest = "normal"
    else:
        highest = max(findings, key=lambda x: level_order[x.level]).level

    return DetectionReport(
        available=True,
        total_anomalies=len(findings),
        highest_level=highest,
        anomalies=findings,
    )


# ───────────────────────────────────────────────────────────────
#  Health
# ───────────────────────────────────────────────────────────────

def health_check() -> Dict[str, Any]:
    return {
        "module": "anomaly_detector",
        "version": "5.0.0",
        "min_points": MIN_POINTS,
        "baseline_days": BASELINE_DAYS,
        "recent_hours": RECENT_WINDOW_HOURS,
        "detectors": [
            "new_place",
            "route_deviation",
            "unexpected_stop",
            "missed_routine",
            "off_hours_activity",
        ],
        "thresholds": {
            "route_deviation_min_m": ROUTE_DEVIATION_MIN_M,
            "unexpected_stop_min_s": UNEXPECTED_STOP_MIN_S,
            "new_place_min_stay_s": NEW_PLACE_MIN_STAY_S,
            "missed_routine_min_rate": MISSED_ROUTINE_MIN_RATE,
            "sleep_hours": [SLEEP_HOUR_START, SLEEP_HOUR_END],
        },
}
