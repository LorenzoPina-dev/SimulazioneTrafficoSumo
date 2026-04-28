"""
geo_utils.py — Utilities geografiche (proiezioni, conversioni, bounding box).
"""

import math
from typing import Tuple, List


EARTH_R = 6_371_000  # metri


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in metri tra due punti geografici."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def latlon_to_mercator(lat: float, lon: float) -> Tuple[float, float]:
    """Proiezione Web Mercator (EPSG:3857) in metri."""
    x = EARTH_R * math.radians(lon)
    y = EARTH_R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def bbox_from_nodes(lats: List[float], lons: List[float], padding: float = 0.01) -> dict:
    """Calcola bounding box con padding percentuale."""
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    dlat = (lat_max - lat_min) * padding
    dlon = (lon_max - lon_min) * padding
    return {
        "lat_min": lat_min - dlat, "lat_max": lat_max + dlat,
        "lon_min": lon_min - dlon, "lon_max": lon_max + dlon,
    }


def degrees_to_meters(degrees: float, lat: float = 45.0) -> float:
    """Conversione approssimativa gradi → metri a una data latitudine."""
    lat_m = EARTH_R * math.radians(degrees)
    lon_m = EARTH_R * math.cos(math.radians(lat)) * math.radians(degrees)
    return (lat_m + lon_m) / 2
