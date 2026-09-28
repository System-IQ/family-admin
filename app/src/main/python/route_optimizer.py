# ═══════════════════════════════════════════════════════════════
#  ULTRA FAMILY TRACKER v5.0 — Route Optimizer
# ═══════════════════════════════════════════════════════════════
#  Purpose:
#    Process raw location points into useful route data:
#      - Simplification (reduce points while keeping shape)
#      - Stop detection
#      - Distance / speed statistics
#      - Segmentation into moving/stopped phases
#      - GPX export
#
#  Design:
#    - Pure functions, no DB access
#    - No external deps
#    - Bounded memory usage
# ═══════════════════════════════════════════════════════════════

import math
import json
import time
from typing import List, Dict, Any, Optional, Tuple

import config as cfg


# ───────────────────────────────────────────────────────────────
#  Geo helpers
# ───────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = cfg.EARTH_RADIUS_M
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _perpendicular_distance_m(p: Dict[str, Any],
                              a: Dict[str, Any],
                              b: Dict[str, Any]) -> float:
    """Perpendicular distance from point p to segment a-b (meters)."""
    if a["lat"] == b["lat"] and a["lon"] == b["lon"]:
        return haversine(p["lat"], p["lon"], a["lat"], a["lon"])
    # Project onto local plane (approximate for small areas)
    lat0 = math.radians(a["lat"])
    y = (p["lat"] - a["lat"]) * 111320.0
    x = (p["lon"] - a["lon"]) * 111320.0 * math.cos(lat0)
    y2 = (b["lat"] - a["lat"]) * 111320.0
    x2 = (b["lon"] - a["lon"]) * 111320.0 * math.cos(lat0)
    seg_len_sq = x2 * x2 + y2 * y2
    if seg_len_sq == 0:
        return math.sqrt(x * x + y * y)
    t = (x * x2 + y * y2) / seg_len_sq
    t = max(0.0, min(1.0, t))
    px = t * x2
    py = t * y2
    return math.sqrt((x - px) ** 2 + (y - py) ** 2)


# ───────────────────────────────────────────────────────────────
#  Route Simplification (Douglas-Peucker)
# ───────────────────────────────────────────────────────────────

def simplify_route(points: List[Dict[str, Any]],
                   tolerance_m: float = 10.0) -> List[Dict[str, Any]]:
    """
    Douglas-Peucker simplification.
    Reduces number of points while preserving shape within tolerance.
    """
    if len(points) < 3 or tolerance_m <= 0:
        return list(points)

    # Iterative implementation to avoid recursion depth issues
    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True

    stack: List[Tuple[int, int]] = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue

        dmax = 0.0
        index = start
        for i in range(start + 1, end):
            d = _perpendicular_distance_m(points[i], points[start], points[end])
            if d > dmax:
                dmax = d
                index = i

        if dmax > tolerance_m:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))

    return [p for i, p in enumerate(points) if keep[i]]


# ───────────────────────────────────────────────────────────────
#  Distance
# ───────────────────────────────────────────────────────────────

def compute_total_distance_m(points: List[Dict[str, Any]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(points)):
        total += haversine(
            points[i - 1]["lat"], points[i - 1]["lon"],
            points[i]["lat"], points[i]["lon"],
        )
    return total


# ───────────────────────────────────────────────────────────────
#  Speed
# ───────────────────────────────────────────────────────────────

def compute_speed_stats(points: List[Dict[str, Any]]) -> Dict[str, float]:
    speeds_kmh: List[float] = []
    for p in points:
        spd = p.get("speed")
        if spd is None:
            continue
        try:
            speeds_kmh.append(float(spd) * 3.6)
        except (TypeError, ValueError):
            continue

    if not speeds_kmh:
        return {"max_kmh": 0.0, "avg_kmh": 0.0, "sample_count": 0}

    return {
        "max_kmh": round(max(speeds_kmh), 2),
        "avg_kmh": round(sum(speeds_kmh) / len(speeds_kmh), 2),
        "sample_count": len(speeds_kmh),
    }


# ───────────────────────────────────────────────────────────────
#  Stop detection
# ───────────────────────────────────────────────────────────────

def detect_stops(points: List[Dict[str, Any]],
                 min_duration_s: int = 300,
                 radius_m: float = 50.0) -> List[Dict[str, Any]]:
    """
    Detect stops: cluster of points within radius for at least min_duration.
    Returns list of stops with lat, lon, start_time, end_time, duration_s.
    """
    if len(points) < 2:
        return []

    sorted_pts = sorted(points, key=lambda p: p["timestamp"])
    stops: List[Dict[str, Any]] = []
    cluster = [sorted_pts[0]]

    for p in sorted_pts[1:]:
        first = cluster[0]
        dist = haversine(first["lat"], first["lon"], p["lat"], p["lon"])
        if dist <= radius_m:
            cluster.append(p)
        else:
            _finalize_cluster(cluster, stops, min_duration_s)
            cluster = [p]

    _finalize_cluster(cluster, stops, min_duration_s)
    return stops


def _finalize_cluster(cluster: List[Dict[str, Any]],
                      stops: List[Dict[str, Any]],
                      min_duration_s: int) -> None:
    if len(cluster) < 2:
        return
    start_ms = cluster[0]["timestamp"]
    end_ms = cluster[-1]["timestamp"]
    duration_s = int((end_ms - start_ms) / 1000)
    if duration_s < min_duration_s:
        return
    avg_lat = sum(p["lat"] for p in cluster) / len(cluster)
    avg_lon = sum(p["lon"] for p in cluster) / len(cluster)
    stops.append({
        "lat": round(avg_lat, 6),
        "lon": round(avg_lon, 6),
        "start_time": start_ms,
        "end_time": end_ms,
        "duration_seconds": duration_s,
        "point_count": len(cluster),
    })


# ───────────────────────────────────────────────────────────────
#  Segmentation (moving vs stopped)
# ───────────────────────────────────────────────────────────────

def segment_route(points: List[Dict[str, Any]],
                  stop_speed_threshold_mps: float = 0.5) -> List[Dict[str, Any]]:
    """
    Break route into segments, each labeled 'moving' or 'stopped'.
    """
    if not points:
        return []

    sorted_pts = sorted(points, key=lambda p: p["timestamp"])
    segments: List[Dict[str, Any]] = []
    current_mode: Optional[str] = None
    current_start = 0

    for i, p in enumerate(sorted_pts):
        spd = float(p.get("speed") or 0)
        mode = "moving" if spd > stop_speed_threshold_mps else "stopped"
        if mode != current_mode:
            if current_mode is not None:
                seg_pts = sorted_pts[current_start:i]
                segments.append(_build_segment(current_mode, seg_pts))
            current_mode = mode
            current_start = i

    if current_mode is not None:
        seg_pts = sorted_pts[current_start:]
        segments.append(_build_segment(current_mode, seg_pts))

    return segments


def _build_segment(mode: str, pts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not pts:
        return {"mode": mode, "point_count": 0}
    return {
        "mode": mode,
        "point_count": len(pts),
        "start_time": pts[0]["timestamp"],
        "end_time": pts[-1]["timestamp"],
        "duration_seconds": int((pts[-1]["timestamp"] - pts[0]["timestamp"]) / 1000),
        "start": {"lat": pts[0]["lat"], "lon": pts[0]["lon"]},
        "end": {"lat": pts[-1]["lat"], "lon": pts[-1]["lon"]},
    }


# ───────────────────────────────────────────────────────────────
#  Bounds
# ───────────────────────────────────────────────────────────────

def compute_bounds(points: List[Dict[str, Any]]) -> Dict[str, float]:
    if not points:
        return {"min_lat": 0.0, "max_lat": 0.0, "min_lon": 0.0, "max_lon": 0.0}
    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    return {
        "min_lat": round(min(lats), 6),
        "max_lat": round(max(lats), 6),
        "min_lon": round(min(lons), 6),
        "max_lon": round(max(lons), 6),
        "center_lat": round((min(lats) + max(lats)) / 2, 6),
        "center_lon": round((min(lons) + max(lons)) / 2, 6),
    }


# ───────────────────────────────────────────────────────────────
#  Full analysis
# ───────────────────────────────────────────────────────────────

def analyze_route(points: List[Dict[str, Any]],
                  simplify_tolerance_m: float = 10.0,
                  stop_min_duration_s: int = 300) -> Dict[str, Any]:
    """
    Produce a complete analysis package for a route.
    """
    if not points:
        return {
            "point_count": 0,
            "distance_m": 0.0,
            "distance_km": 0.0,
            "simplified": [],
            "stops": [],
            "segments": [],
            "bounds": compute_bounds([]),
            "speed": compute_speed_stats([]),
        }

    sorted_pts = sorted(points, key=lambda p: p["timestamp"])

    distance_m = compute_total_distance_m(sorted_pts)
    simplified = simplify_route(sorted_pts, tolerance_m=simplify_tolerance_m)
    stops = detect_stops(sorted_pts, min_duration_s=stop_min_duration_s)
    segments = segment_route(sorted_pts)
    bounds = compute_bounds(sorted_pts)
    speed = compute_speed_stats(sorted_pts)

    return {
        "point_count": len(sorted_pts),
        "simplified_count": len(simplified),
        "distance_m": round(distance_m, 1),
        "distance_km": round(distance_m / 1000, 3),
        "start_time": sorted_pts[0]["timestamp"],
        "end_time": sorted_pts[-1]["timestamp"],
        "duration_seconds": int((sorted_pts[-1]["timestamp"] - sorted_pts[0]["timestamp"]) / 1000),
        "simplified": simplified,
        "stops": stops,
        "stop_count": len(stops),
        "segments": segments,
        "bounds": bounds,
        "speed": speed,
    }


# ───────────────────────────────────────────────────────────────
#  Export
# ───────────────────────────────────────────────────────────────

def to_gpx(points: List[Dict[str, Any]], name: str = "route") -> str:
    """Export points as GPX XML."""
    if not points:
        return '<?xml version="1.0"?><gpx version="1.1"></gpx>'

    sorted_pts = sorted(points, key=lambda p: p["timestamp"])
    from datetime import datetime, timezone
    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append('<gpx version="1.1" creator="FamilyTracker">')
    parts.append(f'  <trk><name>{_xml_escape(name)}</name><trkseg>')
    for p in sorted_pts:
        dt = datetime.fromtimestamp(p["timestamp"] / 1000, tz=timezone.utc)
        parts.append(
            f'    <trkpt lat="{p["lat"]}" lon="{p["lon"]}">'
            f'<time>{dt.isoformat()}</time></trkpt>'
        )
    parts.append('  </trkseg></trk>')
    parts.append('</gpx>')
    return "\n".join(parts)


def to_geojson(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Export as GeoJSON LineString."""
    if not points:
        return {"type": "FeatureCollection", "features": []}
    sorted_pts = sorted(points, key=lambda p: p["timestamp"])
    coords = [[p["lon"], p["lat"]] for p in sorted_pts]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "point_count": len(sorted_pts),
                "start_time": sorted_pts[0]["timestamp"],
                "end_time": sorted_pts[-1]["timestamp"],
            },
        }],
    }


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;"))


# ───────────────────────────────────────────────────────────────
#  Health
# ───────────────────────────────────────────────────────────────

def health_check() -> Dict[str, Any]:
    return {
        "module": "route_optimizer",
        "version": "5.0.0",
        "features": [
            "simplify_route",
            "compute_total_distance_m",
            "compute_speed_stats",
            "detect_stops",
            "segment_route",
            "compute_bounds",
            "analyze_route",
            "to_gpx",
            "to_geojson",
        ],
}
