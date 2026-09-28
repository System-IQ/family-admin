# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — AI Analyzer
# ═══════════════════════════════════════════════════════════════

import math
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

import config as cfg
import database as db


MIN_POINTS_FOR_ANALYSIS = 50
MIN_DAYS_FOR_ROUTINE = 3
MIN_SAMPLES_FOR_PLACE = 3
PLACE_CLUSTER_RADIUS_M = 150.0

ANOMALY_NORMAL_MAX = 0.30
ANOMALY_UNUSUAL_MAX = 0.60
ANOMALY_SUSPICIOUS_MAX = 0.80


@dataclass
class AnalysisResult:
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


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = cfg.EARTH_RADIUS_M
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _hour_of_day(ts_ms: int) -> int:
    # Use LOCAL time (not UTC) so hour analysis matches user's clock
    from datetime import datetime
    return datetime.fromtimestamp(ts_ms / 1000).hour


def _day_of_week(ts_ms: int) -> int:
    # 0 = Monday ... 6 = Sunday (local time)
    from datetime import datetime
    return datetime.fromtimestamp(ts_ms / 1000).weekday()


def _load_recent_points(device_id: str, days: int = 14) -> List[Dict[str, Any]]:
    since = int(time.time() * 1000) - days * 86400 * 1000
    return db.get_locations(device_id, since=since, limit=20000)


def _cluster_points(points: List[Dict[str, Any]],
                    radius_m: float = PLACE_CLUSTER_RADIUS_M) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for p in points:
        lat = p["lat"]
        lon = p["lon"]
        placed = False
        for c in clusters:
            if _haversine(lat, lon, c["lat"], c["lon"]) <= radius_m:
                n = c["count"]
                c["lat"] = (c["lat"] * n + lat) / (n + 1)
                c["lon"] = (c["lon"] * n + lon) / (n + 1)
                c["count"] = n + 1
                c["timestamps"].append(p["timestamp"])
                placed = True
                break
        if not placed:
            clusters.append({
                "lat": lat,
                "lon": lon,
                "count": 1,
                "timestamps": [p["timestamp"]],
            })
    significant = [c for c in clusters if c["count"] >= MIN_SAMPLES_FOR_PLACE]
    significant.sort(key=lambda c: c["count"], reverse=True)
    return significant


def detect_places(device_id: str, days: int = 14) -> AnalysisResult:
    points = _load_recent_points(device_id, days=days)
    if len(points) < MIN_POINTS_FOR_ANALYSIS:
        return AnalysisResult(
            available=False,
            reason=f"insufficient_data (need {MIN_POINTS_FOR_ANALYSIS} points, have {len(points)})",
            data={"point_count": len(points)},
        )
    clusters = _cluster_points(points)
    if not clusters:
        return AnalysisResult(
            available=False,
            reason="no_significant_places_found",
            data={"point_count": len(points)},
        )
    confidence = min(1.0, len(points) / 500.0)
    places = []
    for i, c in enumerate(clusters[:10]):
        hours = defaultdict(int)
        for ts in c["timestamps"]:
            hours[_hour_of_day(ts)] += 1
        top_hours = sorted(hours.items(), key=lambda x: x[1], reverse=True)[:3]
        places.append({
            "rank": i + 1,
            "lat": round(c["lat"], 6),
            "lon": round(c["lon"], 6),
            "visit_count": c["count"],
            "top_hours": [{"hour": h, "count": cnt} for h, cnt in top_hours],
        })
    return AnalysisResult(
        available=True,
        confidence=confidence,
        data={
            "places": places,
            "total_points": len(points),
            "unique_places": len(clusters),
            "days_analyzed": days,
        },
        reasons=[
            f"Analyzed {len(points)} points over {days} days",
            f"Found {len(clusters)} significant locations (>= {MIN_SAMPLES_FOR_PLACE} visits)",
        ],
    )


def learn_routine(device_id: str, days: int = 7) -> AnalysisResult:
    points = _load_recent_points(device_id, days=days)
    if len(points) < MIN_POINTS_FOR_ANALYSIS:
        return AnalysisResult(
            available=False,
            reason=f"insufficient_data (need {MIN_POINTS_FOR_ANALYSIS}, have {len(points)})",
        )
    by_day: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for p in points:
        day_key = p["timestamp"] // (86400 * 1000)
        by_day[day_key].append(p)
    if len(by_day) < MIN_DAYS_FOR_ROUTINE:
        return AnalysisResult(
            available=False,
            reason=f"insufficient_days (need {MIN_DAYS_FOR_ROUTINE} days, have {len(by_day)})",
        )
    first_hours = []
    last_hours = []
    active_hours = []
    for day, day_points in by_day.items():
        if not day_points:
            continue
        day_points.sort(key=lambda x: x["timestamp"])
        first_hours.append(_hour_of_day(day_points[0]["timestamp"]))
        last_hours.append(_hour_of_day(day_points[-1]["timestamp"]))
        hours_seen = set(_hour_of_day(p["timestamp"]) for p in day_points)
        active_hours.extend(hours_seen)
    avg_first = _mean([float(h) for h in first_hours])
    avg_last = _mean([float(h) for h in last_hours])
    hour_counts = defaultdict(int)
    for h in active_hours:
        hour_counts[h] += 1
    most_active_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    confidence = min(1.0, len(by_day) / 7.0)
    return AnalysisResult(
        available=True,
        confidence=confidence,
        data={
            "days_analyzed": len(by_day),
            "avg_first_seen_hour": round(avg_first, 1),
            "avg_last_seen_hour": round(avg_last, 1),
            "most_active_hours": [{"hour": h, "days_active": c} for h, c in most_active_hours],
        },
        reasons=[
            f"Analyzed {len(by_day)} days",
            f"Average first-seen time: {int(avg_first):02d}:00",
            f"Average last-seen time: {int(avg_last):02d}:00",
        ],
    )


def detect_anomaly(device_id: str, lookback_days: int = 14) -> AnalysisResult:
    points = _load_recent_points(device_id, days=lookback_days)
    if len(points) < MIN_POINTS_FOR_ANALYSIS:
        return AnalysisResult(
            available=False,
            reason=f"insufficient_data (need {MIN_POINTS_FOR_ANALYSIS}, have {len(points)})",
        )
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - 86400 * 1000
    recent = [p for p in points if p["timestamp"] >= cutoff]
    baseline = [p for p in points if p["timestamp"] < cutoff]
    if not recent:
        return AnalysisResult(available=False, reason="no_recent_activity")
    if len(baseline) < MIN_POINTS_FOR_ANALYSIS:
        return AnalysisResult(available=False, reason="insufficient_baseline")
    signals = []
    score_components = []
    recent_speeds = [float(p.get("speed") or 0) for p in recent]
    baseline_speeds = [float(p.get("speed") or 0) for p in baseline]
    r_mean_speed = _mean(recent_speeds)
    b_mean_speed = _mean(baseline_speeds)
    b_std_speed = _stddev(baseline_speeds)
    if b_std_speed > 0.5:
        speed_z = abs(r_mean_speed - b_mean_speed) / b_std_speed
        speed_score = min(1.0, speed_z / 3.0)
        score_components.append(("speed", speed_score))
        if speed_score > ANOMALY_NORMAL_MAX:
            signals.append(f"avg speed {r_mean_speed:.1f} vs baseline {b_mean_speed:.1f} m/s")
    recent_hours = set(_hour_of_day(p["timestamp"]) for p in recent)
    baseline_hours = set(_hour_of_day(p["timestamp"]) for p in baseline)
    new_hours = recent_hours - baseline_hours
    if new_hours:
        volume_score = min(1.0, len(new_hours) / 6.0)
        score_components.append(("unusual_hours", volume_score))
        if volume_score > ANOMALY_NORMAL_MAX:
            signals.append(f"active at unusual hours: {sorted(new_hours)}")
    clusters = _cluster_points(baseline)
    if clusters:
        home = clusters[0]
        max_distance = 0.0
        for p in recent:
            d = _haversine(p["lat"], p["lon"], home["lat"], home["lon"])
            if d > max_distance:
                max_distance = d
        baseline_max = 0.0
        for p in baseline:
            d = _haversine(p["lat"], p["lon"], home["lat"], home["lon"])
            if d > baseline_max:
                baseline_max = d
        if baseline_max > 100:
            distance_score = min(1.0, max(0.0, (max_distance - baseline_max) / baseline_max))
            score_components.append(("far_from_home", distance_score))
            if distance_score > ANOMALY_NORMAL_MAX:
                signals.append(
                    f"max distance {max_distance/1000:.1f}km vs typical {baseline_max/1000:.1f}km"
                )
    if not score_components:
        final_score = 0.0
    else:
        weights = {"speed": 0.3, "unusual_hours": 0.3, "far_from_home": 0.4}
        weighted_sum = 0.0
        weight_total = 0.0
        for name, s in score_components:
            w = weights.get(name, 0.3)
            weighted_sum += s * w
            weight_total += w
        final_score = weighted_sum / weight_total if weight_total else 0.0
    level = "normal"
    if final_score > ANOMALY_SUSPICIOUS_MAX:
        level = "high_priority"
    elif final_score > ANOMALY_UNUSUAL_MAX:
        level = "suspicious"
    elif final_score > ANOMALY_NORMAL_MAX:
        level = "unusual"
    confidence = min(1.0, len(baseline) / 500.0)
    return AnalysisResult(
        available=True,
        confidence=confidence,
        data={
            "score": round(final_score, 3),
            "level": level,
            "recent_points": len(recent),
            "baseline_points": len(baseline),
        },
        reasons=signals if signals else ["no significant deviations detected"],
    )


def estimate_eta(device_id: str,
                 target_lat: float, target_lon: float) -> AnalysisResult:
    last = db.get_last_location(device_id)
    if not last:
        return AnalysisResult(available=False, reason="no_location_data")
    distance_m = _haversine(last["lat"], last["lon"], target_lat, target_lon)
    speed = float(last.get("speed") or 0)
    if speed < 0.5:
        points = _load_recent_points(device_id, days=1)
        recent_speeds = [float(p.get("speed") or 0) for p in points if (p.get("speed") or 0) > 0.5]
        if recent_speeds:
            speed = _mean(recent_speeds)
    if speed < 0.5:
        return AnalysisResult(
            available=False,
            reason="device_stationary",
            data={"distance_m": round(distance_m, 1)},
        )
    eta_seconds = distance_m / speed
    confidence = 0.5
    if last.get("accuracy") and last["accuracy"] < 50:
        confidence = 0.7
    return AnalysisResult(
        available=True,
        confidence=confidence,
        data={
            "distance_m": round(distance_m, 1),
            "distance_km": round(distance_m / 1000, 2),
            "speed_mps": round(speed, 2),
            "speed_kmh": round(speed * 3.6, 1),
            "eta_seconds": int(eta_seconds),
            "eta_minutes": int(eta_seconds / 60),
        },
        reasons=[
            f"Straight-line distance: {distance_m/1000:.2f} km",
            f"Current speed: {speed*3.6:.1f} km/h",
            "Estimate assumes straight-line travel and constant speed",
        ],
    )


def full_analysis(device_id: str) -> Dict[str, Any]:
    return {
        "device_id": device_id,
        "places": detect_places(device_id).to_dict(),
        "routine": learn_routine(device_id).to_dict(),
        "anomaly": detect_anomaly(device_id).to_dict(),
        "generated_at": int(time.time() * 1000),
    }


def health_check() -> Dict[str, Any]:
    return {
        "module": "ai_analyzer",
        "version": "5.0.0",
        "min_points": MIN_POINTS_FOR_ANALYSIS,
        "min_days": MIN_DAYS_FOR_ROUTINE,
        "cluster_radius_m": PLACE_CLUSTER_RADIUS_M,
        "anomaly_thresholds": {
            "normal_max": ANOMALY_NORMAL_MAX,
            "unusual_max": ANOMALY_UNUSUAL_MAX,
            "suspicious_max": ANOMALY_SUSPICIOUS_MAX,
        },
    }
