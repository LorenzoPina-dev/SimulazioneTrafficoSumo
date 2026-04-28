"""
camera.py — Gestione della camera 2D: pan, zoom centrato sul mouse, proiezione geo→schermo e geo→NDC.

Architettura coordinate:
  - I VBO OpenGL usano "world NDC": spazio [-1,1] calcolato da world_to_ndc()
  - Il GL renderer applica una matrice uniforme per pan/zoom ogni frame
  - offset_x/y sono mantenuti in world NDC (non in pixel) per coerenza con i VBO
  - Il renderer Pygame usa pixel schermo, con conversione on-the-fly in world_to_screen()
"""

from dataclasses import dataclass
from typing import Tuple

from config.settings import CAMERA, RENDERER


@dataclass
class Camera:
    """
    Gestisce la trasformazione coordinate geografiche → pixel schermo / NDC OpenGL.

    Coordinate mondo:   (lon, lat) float
    Coordinate schermo: (px_x, px_y) int  — usate dal renderer Pygame
    World NDC:          (x, y) ∈ [-1, 1]  — spazio base dei VBO OpenGL
    Device NDC:         world NDC * zoom + offset_ndc  — quello che vede lo shader
    """
    zoom: float      = CAMERA.initial_zoom

    # offset in world NDC — aggiunto DOPO la moltiplicazione per zoom nello shader
    # Corrisponde alla traslazione tx/ty della matrice in _get_transform()
    offset_ndc_x: float = 0.0
    offset_ndc_y: float = 0.0

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

        self.zoom        = 1.0
        self.offset_ndc_x = 0.0
        self.offset_ndc_y = 0.0

    # ──────────────────────────────────────────
    # PROIEZIONE → WORLD NDC (spazio dei VBO OpenGL)
    # ──────────────────────────────────────────

    def world_to_ndc(self, lon: float, lat: float) -> Tuple[float, float]:
        """
        Converte (lon, lat) → (x, y) world NDC ∈ [-1, 1].

        Questi sono i valori scritti nei VBO. Il pan/zoom viene applicato
        come matrice uniform nello shader (vedi _get_transform()), così
        i VBO non vanno mai ricostruiti per pan/zoom.
        """
        span_lon = self._lon_max - self._lon_min
        span_lat = self._lat_max - self._lat_min
        if span_lon == 0 or span_lat == 0:
            return 0.0, 0.0

        x = (lon - self._lon_min) / span_lon * 2.0 - 1.0
        y = (lat - self._lat_min) / span_lat * 2.0 - 1.0

        # Correggi aspect ratio geografico vs finestra
        aspect_geo = (span_lon * self._scale) / (span_lat * self._scale)
        aspect_win = self.width / self.height
        x *= aspect_geo / aspect_win
        return x, y

    def _pixel_to_ndc(self, px: int, py: int) -> Tuple[float, float]:
        """
        Converte coordinate pixel schermo → device NDC [-1,1].
        In OpenGL y=0 è in basso, in Pygame y=0 è in alto → invertiamo y.
        """
        ndc_x = (px / self.width)  * 2.0 - 1.0
        ndc_y = (py / self.height) * 2.0 - 1.0  # ancora in Pygame space (y giù)
        return ndc_x, ndc_y

    # ──────────────────────────────────────────
    # PROIEZIONE → PIXEL SCHERMO (per renderer Pygame e hit-test)
    # ──────────────────────────────────────────

    def world_to_screen(self, lon: float, lat: float) -> Tuple[int, int]:
        """
        Converte (lon, lat) → (px, py) pixel schermo.

        Deve essere coerente con la matrice OpenGL _get_transform():
            device_ndc = world_ndc * zoom + offset_ndc
        Convertiamo device_ndc in pixel:
            px = (device_ndc_x + 1) / 2 * width
            py = (1 - device_ndc_y) / 2 * height  (y invertito)
        """
        wx, wy = self.world_to_ndc(lon, lat)
        dev_x = wx * self.zoom + self.offset_ndc_x
        dev_y = wy * self.zoom + self.offset_ndc_y
        px = int((dev_x + 1.0) * 0.5 * self.width)
        py = int((1.0 - dev_y) * 0.5 * self.height)
        return px, py

    def screen_to_world(self, px: int, py: int) -> Tuple[float, float]:
        """Converte (px, py) pixel schermo → (lon, lat)."""
        # device NDC del punto schermo
        dev_x = (px / self.width)  * 2.0 - 1.0
        dev_y = 1.0 - (py / self.height) * 2.0   # y invertito (GL)
        # world NDC = (device_ndc - offset) / zoom
        wx = (dev_x - self.offset_ndc_x) / self.zoom
        wy = (dev_y - self.offset_ndc_y) / self.zoom
        # world NDC → lon/lat (inverso di world_to_ndc, con correzione aspect)
        span_lon = self._lon_max - self._lon_min
        span_lat = self._lat_max - self._lat_min
        aspect_geo = (span_lon * self._scale) / (span_lat * self._scale)
        aspect_win = self.width / self.height
        wx /= (aspect_geo / aspect_win)  # rimuovi correzione aspect
        lon = (wx + 1.0) * 0.5 * span_lon + self._lon_min
        lat = (wy + 1.0) * 0.5 * span_lat + self._lat_min
        return lon, lat

    # ──────────────────────────────────────────
    # PAN / ZOOM
    # ──────────────────────────────────────────

    def pan(self, dx_px: float, dy_px: float) -> None:
        """Pan in pixel schermo → converte in delta NDC."""
        self.offset_ndc_x += (dx_px / self.width)  * 2.0
        self.offset_ndc_y -= (dy_px / self.height) * 2.0  # y invertito

    def zoom_at(self, px: int, py: int, factor: float) -> None:
        """
        Zoom centrato sul punto schermo (px, py).

        Il punto sotto il cursore deve restare fermo nel device NDC.
        In device NDC il punto del mouse vale:
            dev = world_ndc * zoom + offset_ndc

        Dopo il cambio di zoom (zoom_new = zoom * factor):
            dev_new = world_ndc * zoom_new + offset_ndc_new

        Per ancorare: dev_new == dev  →  risolviamo per offset_ndc_new:
            offset_ndc_new = dev - world_ndc * zoom_new
                           = (world_ndc * zoom + offset_ndc) - world_ndc * zoom_new
                           = offset_ndc + world_ndc * (zoom - zoom_new)
                           = offset_ndc - world_ndc * zoom * (factor - 1)

        world_ndc del mouse = (dev - offset_ndc) / zoom
        Sostituendo:
            offset_ndc_new = offset_ndc - ((dev - offset_ndc) / zoom) * zoom * (factor - 1)
                           = offset_ndc - (dev - offset_ndc) * (factor - 1)
                           = offset_ndc * factor + dev * (1 - factor)
                           = dev + factor * (offset_ndc - dev)

        Dove dev è il device NDC del mouse (calcolato dai pixel):
            dev_x = px/width * 2 - 1
            dev_y = 1 - py/height * 2   (y invertito GL)
        """
        new_zoom = max(CAMERA.zoom_min, min(CAMERA.zoom_max, self.zoom * factor))

        # Device NDC del cursore (spazio GL: y verso l'alto)
        dev_x = (px / self.width)  * 2.0 - 1.0
        dev_y = 1.0 - (py / self.height) * 2.0

        # Aggiorna offset mantenendo il punto sotto il mouse fisso
        self.offset_ndc_x = dev_x + factor * (self.offset_ndc_x - dev_x)
        self.offset_ndc_y = dev_y + factor * (self.offset_ndc_y - dev_y)
        self.zoom = new_zoom

    def is_on_screen(self, px: int, py: int, margin: int = 20) -> bool:
        return (
            -margin <= px <= self.width  + margin and
            -margin <= py <= self.height + margin
        )

    @property
    def zoom_tier(self) -> int:
        """Livello di LOD discreto."""
        if self.zoom < 0.3:  return 0
        if self.zoom < 1.0:  return 1
        if self.zoom < 3.0:  return 2
        if self.zoom < 8.0:  return 3
        return 4

    # Alias per compatibilità con il renderer Pygame (usa offset_x/y in pixel)
    @property
    def offset_x(self) -> float:
        """Offset X in pixel (solo per compatibilità MapRenderer Pygame)."""
        return self.offset_ndc_x * self.width * 0.5

    @offset_x.setter
    def offset_x(self, v: float) -> None:
        self.offset_ndc_x = v / (self.width * 0.5)

    @property
    def offset_y(self) -> float:
        """Offset Y in pixel (solo per compatibilità MapRenderer Pygame)."""
        return -self.offset_ndc_y * self.height * 0.5

    @offset_y.setter
    def offset_y(self, v: float) -> None:
        self.offset_ndc_y = -v / (self.height * 0.5)
