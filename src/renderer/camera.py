"""
camera.py — Gestione della camera 2D: pan, zoom, proiezione geo→schermo e geo→NDC.
"""

import math
from dataclasses import dataclass, field
from typing import Tuple

from config.settings import CAMERA, RENDERER


@dataclass
class Camera:
    """
    Gestisce la trasformazione coordinate geografiche → pixel schermo / NDC OpenGL.

    Coordinate mondo:   (lon, lat) float
    Coordinate schermo: (px_x, px_y) int
    Coordinate NDC:     (x, y) ∈ [-1, 1]  (usate dal GL renderer)
    """
    zoom: float      = CAMERA.initial_zoom
    offset_x: float  = 0.0
    offset_y: float  = 0.0
    width: int       = RENDERER.window_width
    height: int      = RENDERER.window_height

    _lon_min: float  = 0.0
    _lon_max: float  = 1.0
    _lat_min: float  = 0.0
    _lat_max: float  = 1.0
    _scale: float    = 1.0   # pixel per grado geografico (zoom=1)

    # ──────────────────────────────────────────
    # INIT / FIT
    # ──────────────────────────────────────────

    def fit_to_bbox(
        self,
        lon_min: float, lat_min: float,
        lon_max: float, lat_max: float,
        padding: float = 0.05,
    ) -> None:
        """Centra e adatta lo zoom per mostrare l'intera mappa."""
        self._lon_min = lon_min - padding * (lon_max - lon_min)
        self._lon_max = lon_max + padding * (lon_max - lon_min)
        self._lat_min = lat_min - padding * (lat_max - lat_min)
        self._lat_max = lat_max + padding * (lat_max - lat_min)

        span_lon = self._lon_max - self._lon_min
        span_lat = self._lat_max - self._lat_min
        if span_lon == 0 or span_lat == 0:
            return

        scale_x = self.width  / span_lon
        scale_y = self.height / span_lat
        self._scale = min(scale_x, scale_y)

        self.zoom     = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0

    # ──────────────────────────────────────────
    # PROIEZIONE → PIXEL SCHERMO
    # ──────────────────────────────────────────

    def world_to_screen(self, lon: float, lat: float) -> Tuple[int, int]:
        """Converte (lon, lat) → (px, py) pixel schermo."""
        sx = (lon - self._lon_min) * self._scale * self.zoom + self.offset_x
        sy = (self._lat_max - lat) * self._scale * self.zoom + self.offset_y
        return int(sx), int(sy)

    def screen_to_world(self, px: int, py: int) -> Tuple[float, float]:
        """Converte (px, py) pixel schermo → (lon, lat)."""
        lon = (px - self.offset_x) / (self._scale * self.zoom) + self._lon_min
        lat = self._lat_max - (py - self.offset_y) / (self._scale * self.zoom)
        return lon, lat

    # ──────────────────────────────────────────
    # PROIEZIONE → NDC (Normalized Device Coordinates, per OpenGL)
    # ──────────────────────────────────────────

    def world_to_ndc(self, lon: float, lat: float) -> Tuple[float, float]:
        """
        Converte (lon, lat) → (x, y) NDC ∈ [-1, 1].
        Usato per costruire i VBO in spazio "world NDC" fisso.
        Il pan/zoom viene poi applicato come uniform matrix nello shader,
        evitando di rebuilddare il VBO ad ogni frame.
        """
        span_lon = self._lon_max - self._lon_min
        span_lat = self._lat_max - self._lat_min
        if span_lon == 0 or span_lat == 0:
            return 0.0, 0.0
        x = (lon - self._lon_min) / span_lon * 2.0 - 1.0
        y = (lat - self._lat_min) / span_lat * 2.0 - 1.0
        # Correggi aspect ratio: lo span in gradi non è isotropo
        # (1 grado lon ≠ 1 grado lat in pixel) — scaliamo x
        aspect_geo = (span_lon * self._scale) / (span_lat * self._scale)
        aspect_win = self.width / self.height
        x *= aspect_geo / aspect_win
        return x, y

    def ndc_to_world(self, x: float, y: float) -> Tuple[float, float]:
        """Inverso di world_to_ndc (approssimato, non tiene conto aspect fix)."""
        span_lon = self._lon_max - self._lon_min
        span_lat = self._lat_max - self._lat_min
        lon = (x + 1.0) / 2.0 * span_lon + self._lon_min
        lat = (y + 1.0) / 2.0 * span_lat + self._lat_min
        return lon, lat

    # ──────────────────────────────────────────
    # PAN / ZOOM
    # ──────────────────────────────────────────

    def pan(self, dx: float, dy: float) -> None:
        self.offset_x += dx
        self.offset_y += dy

    def zoom_at(self, px: int, py: int, factor: float) -> None:
        """Zoom centrato sul punto schermo (px, py)."""
        new_zoom = max(CAMERA.zoom_min, min(CAMERA.zoom_max, self.zoom * factor))
        ratio = new_zoom / self.zoom
        self.offset_x = px - ratio * (px - self.offset_x)
        self.offset_y = py - ratio * (py - self.offset_y)
        self.zoom = new_zoom

    def is_on_screen(self, px: int, py: int, margin: int = 20) -> bool:
        return (
            -margin <= px <= self.width  + margin and
            -margin <= py <= self.height + margin
        )

    @property
    def zoom_tier(self) -> int:
        """Livello di LOD discreto (usato per invalidare VBO selettivamente)."""
        if self.zoom < 0.3:   return 0
        if self.zoom < 1.0:   return 1
        if self.zoom < 3.0:   return 2
        if self.zoom < 8.0:   return 3
        return 4
