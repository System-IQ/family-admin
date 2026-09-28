# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Pattern Detector
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Detect higher-level behavioral patterns distinct from
#    ai_analyzer's clustering:
#      • Trips (departure from a place → arrival at another)
#      • Recurring routes (A → B happens often at time X)
#      • Weekly patterns (each day-of-week has distinct behavior)
#      • Time slots (morning / afternoon / evening / night)
#      • Peak hours per detected place
#
#  Principles:
#    1. All results derived strictly from DB data
#    2. No predictions without minimum data
#    3. Explainable (reasons array on every result)
#    4. Deterministic (same input → same output)
#    5. No external ML libraries
# ═══════════════════════════════════════════════════════════════

import math
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

import config as cfg
import database as db


# ───────────────────────────────────────────────────────────────
#  Constants
# ───────────────────────────────────────────────────────────────

MIN_POINTS = 30                 # less than this → not enough
MIN_TRIPS_FOR_ROUTE = 3         # same route repeated this many times → "recurring"
MIN_DAYS_FOR_WEEKLY = 7         # at least a week to talk about "weekly pattern"

# Reuse ai_analyzer's clustering logic for place identification
PLACE_CLUSTER_RADIUS_M = 150.0
MIN_SAMPLES_FOR_PLACE = 3

# Trip boundaries
STOP_SPEED_THRESHOLD_MPS = 0.5
MIN_STOP_DURATION_S = 300       # 5 minutes at a place = a "visit"
MIN_TRIP_DURATION_S = 60        # a trip shorter than this isn't meaningful

# Time slots (hour ranges, inclusive-start exclusive-end)
TIME_SLOTS = [
    ("night",     0,  6),
    ("morning",   6,  12),
    ("afternoon", 12, 17),
    ("evening",   17, 22),
    ("late",      22, 24),
]

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]


# ───────────────────────────────────────────────────────────────
#  Result wrapper (identical shape to ai_analyzer for consistency)
# ───────────────────────────────────────────────────────────────

@dataclass
class PatternResult:
    available: bool = False
    reason: str = ""
    confidence: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    generated_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "data": self.data,
            "reasons": self.reasons,
            "generated_at": self.generated_at or int(time.time() * 1000),
        }


# ───────────────────────────────────────────────────────────────
#  Geo / time helpers
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
    return (ts_ms // (3600 * 1000)) % 24


def _day_of_week(ts_ms: int) -> int:
    # 0 = Monday ... 6 = Sunday
    return ((ts_ms // (86400 * 1000)) + 3) % 7


def _slot_of_hour(hour: int) -> str:
    for name, lo, hi in TIME_SLOTS:
        if lo <= hour < hi:
            return name
    return "unknown"


# ───────────────────────────────────────────────────────────────
#  Load points
# ───────────────────────────────────────────────────────────────

def _load_points(device_id: str, days: int) -> List[Dict[str, Any]]:
    since = int(time.time() * 1000) - days * 86400 * 1000
    pts = db.get_locations(device_id, since=since, limit=50000)
    pts.sort(key=lambda p: p["timestamp"])
    return pts


# ───────────────────────────────────────────────────────────────
#  Place clustering (self-contained, so this module doesn't
#  depend on ai_analyzer)
# ───────────────────────────────────────────────────────────────

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
                "lat": p["lat"],
                "lon": p["lon"],
                "count": 1,
                "timestamps": [p["timestamp"]],
            })
    significant = [c for c in clusters if c["count"] >= MIN_SAMPLES_FOR_PLACE]
    significant.sort(key=lambda c: c["count"], reverse=True)
    # assign stable id by rank
    for i, c in enumerate(significant):
        c["place_id"] = f"P{i+1}"
    return significant


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
#  Trip detection
# ───────────────────────────────────────────────────────────────

def _detect_visits(points: List[Dict[str, Any]],
                   places: List[Dict[str, Any]],
                   min_duration_s: int = MIN_STOP_DURATION_S
                   ) -> List[Dict[str, Any]]:
    """
    Walk through points; when the device stays near a place for at
    least min_duration_s, record a Visit:
        { place_id, start_time, end_time, duration_s }
    """
    visits: List[Dict[str, Any]] = []
    current_place_id: Optional[str] = None
    current_start: Optional[int] = None
    last_ts: Optional[int] = None

    for p in points:
        place = _nearest_place(p["lat"], p["lon"], places)
        pid = place["place_id"] if place else None

        if pid != current_place_id:
            # close previous visit
            if current_place_id is not None and current_start is not None and last_ts is not None:
                dur = (last_ts - current_start) // 1000
                if dur >= min_duration_s:
                    visits.append({
                        "place_id": current_place_id,
                        "start_time": current_start,
                        "end_time": last_ts,
                        "duration_seconds": dur,
                    })
            current_place_id = pid
            current_start = p["timestamp"] if pid else None

        last_ts = p["timestamp"]

    # close last visit
    if current_place_id is not None and current_start is not None and last_ts is not None:
        dur = (last_ts - current_start) // 1000
        if dur >= min_duration_s:
            visits.append({
                "place_id": current_place_id,
                "start_time": current_start,
                "end_time": last_ts,
                "duration_seconds": dur,
            })

    return visits


def _visits_to_trips(visits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Consecutive visits produce a Trip:
        from_place -> to_place at departure_time
    """
    trips: List[Dict[str, Any]] = []
    for i in range(1, len(visits)):
        a = visits[i - 1]
        b = visits[i]
        if a["place_id"] == b["place_id"]:
            continue
        departure = a["end_time"]
        arrival = b["start_time"]
        dur = (arrival - departure) // 1000
        if dur < MIN_TRIP_DURATION_S:
            continue
        trips.append({
            "from_place": a["place_id"],
            "to_place": b["place_id"],
            "departure_time": departure,
            "arrival_time": arrival,
            "duration_seconds": dur,
        })
    return trips


# ───────────────────────────────────────────────────────────────
#  Public: trips for a device
# ───────────────────────────────────────────────────────────────

def detect_trips(device_id: str, days: int = 14) -> PatternResult:
    pts = _load_points(device_id, days)
    if len(pts) < MIN_POINTS:
        return PatternResult(
            available=False,
            reason=f"insufficient_data (have {len(pts)}, need {MIN_POINTS})",
        )

    places = _cluster_places(pts)
    if len(places) < 2:
        return PatternResult(
            available=False,
            reason="fewer_than_2_places_detected",
            data={"places_found": len(places)},
        )

    visits = _detect_visits(pts, places)
    trips = _visits_to_trips(visits)

    confidence = min(1.0, len(trips) / 20.0)

    return PatternResult(
        available=True,
        confidence=confidence,
        data={
            "places": [
                {"id": p["place_id"], "lat": round(p["lat"], 6),
                 "lon": round(p["lon"], 6), "visits": p["count"]}
                for p in places
            ],
            "visit_count": len(visits),
            "trip_count": len(trips),
            "trips": trips[:50],  # cap
        },
        reasons=[
            f"Detected {len(places)} places",
            f"Detected {len(visits)} visits (>= {MIN_STOP_DURATION_S}s)",
            f"Detected {len(trips)} trips",
        ],
    )


# ───────────────────────────────────────────────────────────────
#  Public: recurring routes
# ───────────────────────────────────────────────────────────────

def detect_recurring_routes(device_id: str,
                            days: int = 14,
                            min_occurrences: int = MIN_TRIPS_FOR_ROUTE
                            ) -> PatternResult:
    res = detect_trips(device_id, days)
    if not res.available:
        return res

    trips = res.data["trips"]
    if len(trips) < min_occurrences:
        return PatternResult(
            available=False,
            reason=f"insufficient_trips (have {len(trips)}, need {min_occurrences})",
        )

    # Group by (from_place, to_place)
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for t in trips:
        groups[(t["from_place"], t["to_place"])].append(t)

    recurring: List[Dict[str, Any]] = []
    for (a, b), ts in groups.items():
        if len(ts) < min_occurrences:
            continue
        # Departure hours
        dep_hours = [_hour_of_day(t["departure_time"]) for t in ts]
        dur_seconds = [t["duration_seconds"] for t in ts]
        recurring.append({
            "from_place": a,
            "to_place": b,
            "occurrences": len(ts),
            "avg_departure_hour": round(sum(dep_hours) / len(dep_hours), 1),
            "min_departure_hour": min(dep_hours),
            "max_departure_hour": max(dep_hours),
            "avg_duration_seconds": int(sum(dur_seconds) / len(dur_seconds)),
        })

    if not recurring:
        return PatternResult(
            available=False,
            reason="no_route_repeats_enough",
        )

    recurring.sort(key=lambda r: r["occurrences"], reverse=True)

    return PatternResult(
        available=True,
        confidence=min(1.0, sum(r["occurrences"] for r in recurring) / 30.0),
        data={
            "recurring_routes": recurring,
            "unique_routes": len(recurring),
        },
        reasons=[
            f"{len(recurring)} routes repeated >= {min_occurrences} times",
            f"Top route: {recurring[0]['from_place']} → {recurring[0]['to_place']} "
            f"({recurring[0]['occurrences']} times)",
        ],
    )


# ───────────────────────────────────────────────────────────────
#  Public: weekly pattern
# ───────────────────────────────────────────────────────────────

def learn_weekly_pattern(device_id: str, days: int = 21) -> PatternResult:
    pts = _load_points(device_id, days)
    if len(pts) < MIN_POINTS:
        return PatternResult(
            available=False,
            reason=f"insufficient_data (have {len(pts)}, need {MIN_POINTS})",
        )

    # Group points by day-of-week
    by_dow: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for p in pts:
        by_dow[_day_of_week(p["timestamp"])].append(p)

    # Need at least a week of distinct days
    covered_days = [d for d in range(7) if len(by_dow.get(d, [])) > 0]
    if len(covered_days) < 5:
        return PatternResult(
            available=False,
            reason=f"insufficient_week_coverage (have {len(covered_days)} days, need 5)",
        )

    per_day: List[Dict[str, Any]] = []
    for dow in range(7):
        dpts = by_dow.get(dow, [])
        if not dpts:
            continue
        hours = [_hour_of_day(p["timestamp"]) for p in dpts]
        first = min(hours)
        last = max(hours)
        active_hours = sorted(set(hours))
        per_day.append({
            "day_index": dow,
            "day_name": DAY_NAMES[dow],
            "point_count": len(dpts),
            "first_hour": first,
            "last_hour": last,
            "active_hours_count": len(active_hours),
        })

    if not per_day:
        return PatternResult(
            available=False,
            reason="no_data_per_day",
        )

    # Find the day with most activity
    most_active = max(per_day, key=lambda x: x["point_count"])
    least_active = min(per_day, key=lambda x: x["point_count"])

    # Detect weekday vs weekend contrast (Mon-Fri vs Sat-Sun)
    weekday_points = sum(
        d["point_count"] for d in per_day if d["day_index"] <= 4
    )
    weekend_points = sum(
        d["point_count"] for d in per_day if d["day_index"] >= 5
    )

    confidence = min(1.0, len(covered_days) / 7.0)

    return PatternResult(
        available=True,
        confidence=confidence,
        data={
            "days_covered": len(covered_days),
            "per_day": per_day,
            "most_active_day": most_active["day_name"],
            "least_active_day": least_active["day_name"],
            "weekday_points": weekday_points,
            "weekend_points": weekend_points,
        },
        reasons=[
            f"Covered {len(covered_days)} distinct weekdays",
            f"Most active: {most_active['day_name']} ({most_active['point_count']} pts)",
            f"Least active: {least_active['day_name']} ({least_active['point_count']} pts)",
        ],
    )


# ───────────────────────────────────────────────────────────────
#  Public: time-slot distribution
# ───────────────────────────────────────────────────────────────

def analyze_time_slots(device_id: str, days: int = 14) -> PatternResult:
    pts = _load_points(device_id, days)
    if len(pts) < MIN_POINTS:
        return PatternResult(
            available=False,
            reason=f"insufficient_data (have {len(pts)}, need {MIN_POINTS})",
        )

    counts: Dict[str, int] = defaultdict(int)
    hour_hist: Dict[int, int] = defaultdict(int)

    for p in pts:
        h = _hour_of_day(p["timestamp"])
        counts[_slot_of_hour(h)] += 1
        hour_hist[h] += 1

    total = sum(counts.values())
    distribution = []
    for name, _lo, _hi in TIME_SLOTS:
        c = counts.get(name, 0)
        distribution.append({
            "slot": name,
            "count": c,
            "percent": round(100.0 * c / total, 1) if total else 0.0,
        })

    peak_hour = max(hour_hist.items(), key=lambda x: x[1])[0]

    return PatternResult(
        available=True,
        confidence=min(1.0, len(pts) / 500.0),
        data={
            "distribution": distribution,
            "total_points": total,
            "peak_hour": peak_hour,
            "hour_histogram": dict(sorted(hour_hist.items())),
        },
        reasons=[
            f"Analyzed {total} points over {days} days",
            f"Peak hour: {peak_hour}:00",
            f"Most active slot: {max(distribution, key=lambda x: x['count'])['slot']}",
        ],
    )


# ───────────────────────────────────────────────────────────────
#  Public: peak hours per place
# ───────────────────────────────────────────────────────────────

def peak_hours_per_place(device_id: str, days: int = 14) -> PatternResult:
    pts = _load_points(device_id, days)
    if len(pts) < MIN_POINTS:
        return PatternResult(
            available=False,
            reason=f"insufficient_data (have {len(pts)}, need {MIN_POINTS})",
        )

    places = _cluster_places(pts)
    if not places:
        return PatternResult(
            available=False,
            reason="no_significant_places",
        )

    out: List[Dict[str, Any]] = []
    for place in places:
        hour_hist: Dict[int, int] = defaultdict(int)
        for p in pts:
            if _haversine(p["lat"], p["lon"], place["lat"], place["lon"]) <= PLACE_CLUSTER_RADIUS_M:
                hour_hist[_hour_of_day(p["timestamp"])] += 1
        if not hour_hist:
            continue
        top = sorted(hour_hist.items(), key=lambda x: x[1], reverse=True)[:3]
        out.append({
            "place_id": place["place_id"],
            "lat": round(place["lat"], 6),
            "lon": round(place["lon"], 6),
            "total_points": place["count"],
            "top_hours": [{"hour": h, "count": c} for h, c in top],
        })

    return PatternResult(
        available=True,
        confidence=min(1.0, len(pts) / 500.0),
        data={"places": out},
        reasons=[f"Analyzed {len(places)} places over {days} days"],
    )


# ───────────────────────────────────────────────────────────────
#  Convenience: full report
# ───────────────────────────────────────────────────────────────

def full_report(device_id: str, days: int = 14) -> Dict[str, Any]:
    return {
        "device_id": device_id,
        "trips": detect_trips(device_id, days).to_dict(),
        "recurring_routes": detect_recurring_routes(device_id, days).to_dict(),
        "weekly": learn_weekly_pattern(device_id, days).to_dict(),
        "time_slots": analyze_time_slots(device_id, days).to_dict(),
        "peak_hours": peak_hours_per_place(device_id, days).to_dict(),
        "generated_at": int(time.time() * 1000),
    }


def health_check() -> Dict[str, Any]:
    return {
        "module": "pattern_detector",
        "version": "5.0.0",
        "min_points": MIN_POINTS,
        "min_trips_for_route": MIN_TRIPS_FOR_ROUTE,
        "min_days_for_weekly": MIN_DAYS_FOR_WEEKLY,
        "features": [
            "detect_trips",
            "detect_recurring_routes",
            "learn_weekly_pattern",
            "analyze_time_slots",
            "peak_hours_per_place",
            "full_report",
        ],
}
