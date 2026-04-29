"""
overpass_fetcher.py — Recupera dati stradali da OpenStreetMap tramite Overpass API.
Supporta fallback automatico su endpoint multipli e query bbox come alternativa all'area search.
"""

import json
import time
import logging
import hashlib
import os
import requests
from typing import Optional, Dict, Any

from config.settings import OVERPASS, DATA_SOURCES, CACHE_DIR, CityConfig
from fetchers.nominatim_fetcher import NominatimFetcher

log = logging.getLogger(__name__)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


class OverpassFetcher:
    """
    Interroga l'API Overpass per ottenere la rete stradale di una città.
    - Fallback automatico su endpoint alternativi
    - Strategia bbox come fallback alla area-query (più affidabile)
    - Cache locale per evitare richieste ripetute

    Attributo pubblico:
        last_cache_path — percorso del file JSON in cache dell'ultima richiesta.
                          Usato da SumoNetExporter per passare i dati grezzi a netconvert.
    """

    def __init__(self):
        self.timeout          = OVERPASS.timeout_seconds
        self.retries          = OVERPASS.max_retries
        self.delay            = OVERPASS.retry_delay
        self.last_cache_path: Optional[str] = None   # ← esposto alla pipeline
        self._nominatim       = NominatimFetcher()
        os.makedirs(CACHE_DIR, exist_ok=True)

    # ──────────────────────────────────────────
    # INTERFACCIA PUBBLICA
    # ──────────────────────────────────────────

    def fetch_road_network(self, city: CityConfig) -> Dict[str, Any]:
        """
        Restituisce dict con chiave 'elements' (lista nodi/vie OSM).
        Usa cache se disponibile e non scaduta.
        Imposta self.last_cache_path con il percorso del file JSON.
        """
        bbox = city.bbox
        if bbox is None:
            log.info("Risolvo bbox per '%s' via Nominatim...", city.osm_area_query)
            bbox = self._nominatim.get_city_bbox(city.osm_area_query)
            if bbox:
                log.info("Bbox: lat %.4f–%.4f  lon %.4f–%.4f",
                         bbox[0], bbox[1], bbox[2], bbox[3])
            else:
                log.warning("Bbox non trovata, uso area-query come fallback")

        query      = self._build_query(city, bbox)
        cache_path = self._cache_path(query)
        self.last_cache_path = cache_path   # sempre impostato, esista o meno

        if DATA_SOURCES.use_cache and self._cache_valid(cache_path):
            log.info("Cache hit: carico dati OSM da %s", cache_path)
            return self._load_cache(cache_path)

        log.info("Fetching rete stradale per '%s'...", city.osm_area_query)
        data = self._execute_with_fallback(query)

        if DATA_SOURCES.use_cache:
            self._save_cache(cache_path, data)

        return data

    # ──────────────────────────────────────────
    # COSTRUZIONE QUERY OVERPASS QL
    # ──────────────────────────────────────────

    def _build_query(self, city: CityConfig, bbox: Optional[tuple]) -> str:
        filters = OVERPASS.highway_filters
        timeout = self.timeout

        if bbox:
            lat_min, lat_max, lon_min, lon_max = bbox
            area = f"({lat_min},{lon_min},{lat_max},{lon_max})"
            ways = "\n  ".join(f'way["highway"="{hw}"]{area};' for hw in filters)
            return f"""[out:json][timeout:{timeout}];
(
  {ways}
);
(._;>;);
out body;
"""
        else:
            ways = "\n  ".join(
                f'way["highway"="{hw}"](area.searchArea);' for hw in filters
            )
            return f"""[out:json][timeout:{timeout}];
area["name"="{city.name}"]["boundary"="administrative"]->.searchArea;
(
  {ways}
);
(._;>;);
out body;
"""

    # ──────────────────────────────────────────
    # HTTP CON FALLBACK
    # ──────────────────────────────────────────

    def _execute_with_fallback(self, query: str) -> Dict[str, Any]:
        last_exc = None
        for endpoint in OVERPASS_ENDPOINTS:
            log.info("Provo endpoint: %s", endpoint)
            try:
                return self._execute_query(endpoint, query)
            except Exception as exc:
                log.warning("Endpoint %s fallito: %s", endpoint, exc)
                last_exc = exc
                time.sleep(2)
        raise RuntimeError(
            f"Tutti gli endpoint Overpass non raggiungibili. Ultimo errore: {last_exc}"
        ) from last_exc

    def _execute_query(self, endpoint: str, query: str) -> Dict[str, Any]:
        for attempt in range(1, self.retries + 1):
            try:
                log.debug("POST %s (tentativo %d/%d)", endpoint, attempt, self.retries)
                resp = requests.post(
                    endpoint,
                    data={"data": query},
                    timeout=self.timeout,
                    headers={"User-Agent": "CityGraphExplorer/1.0"},
                )
                resp.raise_for_status()
                data = resp.json()
                n    = len(data.get("elements", []))
                log.info("Overpass OK: %d elementi da %s", n, endpoint)
                if n == 0:
                    raise ValueError("Risposta vuota (0 elementi)")
                return data
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                log.warning("Tentativo %d/%d: %s", attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(self.delay * attempt)
                else:
                    raise
            except requests.exceptions.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                if code in (429, 502, 503, 504) and attempt < self.retries:
                    log.warning("HTTP %d, attendo %ds...", code, self.delay * attempt)
                    time.sleep(self.delay * attempt)
                else:
                    raise

    # ──────────────────────────────────────────
    # CACHE
    # ──────────────────────────────────────────

    def _cache_path(self, query: str) -> str:
        digest = hashlib.md5(query.encode()).hexdigest()[:12]
        return os.path.join(CACHE_DIR, f"osm_{digest}.json")

    def _cache_valid(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        return (time.time() - os.path.getmtime(path)) / 3600 < DATA_SOURCES.cache_ttl_hours

    def _load_cache(self, path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_cache(self, path: str, data: Dict[str, Any]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        log.debug("Cache salvata: %s", path)
