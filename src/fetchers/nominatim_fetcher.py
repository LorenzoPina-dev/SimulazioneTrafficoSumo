"""
nominatim_fetcher.py — Geocoding e info aggiuntive tramite Nominatim (OSM).
"""

import time
import logging
import requests
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
HEADERS = {"User-Agent": "CityGraphExplorer/1.0 (github.com/testSumo)"}


class NominatimFetcher:
    """
    Fornisce:
    - Geocoding forward (nome → coordinate)
    - Reverse geocoding (coordinate → indirizzo)
    - Bounding box di una città
    - POI (punti di interesse) base
    """

    def __init__(self, request_delay: float = 1.1):
        self.delay = request_delay

    # ──────────────────────────────────────────
    # INTERFACCIA PUBBLICA
    # ──────────────────────────────────────────

    def geocode(self, query: str) -> Optional[Dict[str, Any]]:
        """Restituisce il primo risultato di geocoding per la query."""
        params = {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 1,
        }
        results = self._get("/search", params)
        if results:
            return results[0]
        return None

    def get_city_bbox(self, city_query: str) -> Optional[tuple]:
        """
        Restituisce (lat_min, lat_max, lon_min, lon_max) della città,
        oppure None se non trovata.

        Nominatim boundingbox formato: [lat_min, lat_max, lon_min, lon_max]
        """
        result = self.geocode(city_query)
        if result and "boundingbox" in result:
            bb = result["boundingbox"]
            # bb = [lat_min, lat_max, lon_min, lon_max]
            lat_min = float(bb[0])
            lat_max = float(bb[1])
            lon_min = float(bb[2])
            lon_max = float(bb[3])
            log.info("Bbox '%s': lat %.4f–%.4f  lon %.4f–%.4f",
                     city_query, lat_min, lat_max, lon_min, lon_max)
            return (lat_min, lat_max, lon_min, lon_max)
        log.warning("Bbox non trovata per '%s'", city_query)
        return None

    def reverse_geocode(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        params = {"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1}
        return self._get("/reverse", params)

    def fetch_pois(self, city_query: str, amenity_types: List[str] = None) -> List[Dict]:
        """
        Cerca POI tramite Nominatim (limitato).
        """
        if amenity_types is None:
            amenity_types = ["restaurant", "hospital", "school", "park", "museum"]
        pois = []
        for amenity in amenity_types:
            params = {
                "q": f"{amenity} in {city_query}",
                "format": "jsonv2",
                "limit": 20,
            }
            results = self._get("/search", params)
            if results:
                for r in results:
                    r["_amenity_type"] = amenity
                    pois.append(r)
            time.sleep(self.delay)
        return pois

    # ──────────────────────────────────────────
    # HTTP
    # ──────────────────────────────────────────

    def _get(self, path: str, params: dict) -> Any:
        url = NOMINATIM_BASE + path
        try:
            time.sleep(self.delay)
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            log.warning("Nominatim %s fallito: %s", path, exc)
            return None
