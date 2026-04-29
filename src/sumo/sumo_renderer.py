"""
sumo_renderer.py — Renderer interattivo Pygame per la simulazione SUMO.

Obiettivi della vista:
  - mappa più leggibile, con resa vicina a un viewer stradale moderno
  - corsie separate dove disponibili, frecce di marcia e nomi via per livello di zoom
  - veicoli orientati e dimensionati in base ai dati reali di SUMO
  - overlay per semafori, STOP e dare precedenza
"""

import math
import os
import logging
from typing import Optional, List, Dict, Tuple, Any

import pygame
import networkx as nx

from sumo.net_exporter import SumoNetExporter
from sumo.scenario_builder import ScenarioBuilder, VEHICLE_TYPES

log = logging.getLogger(__name__)

INITIAL_WINDOW_W = 1500
INITIAL_WINDOW_H = 920
PANEL_W = 300

MAP_BG = (239, 235, 226)
MAP_GRID = (233, 229, 220)
MAP_WATERMARK = (226, 220, 209)
PANEL_BG = (25, 31, 47)
PANEL_BORDER = (50, 61, 88)
TEXT = (224, 229, 241)
TEXT_DIM = (138, 149, 174)
TEXT_DARK = (59, 67, 83)
TEXT_FAINT = (92, 99, 112)
BTN = (51, 69, 112)
BTN_HOVER = (73, 97, 150)
BTN_ACTIVE = (38, 176, 104)
BTN_STOP = (184, 76, 68)
BTN_DISABLED = (63, 71, 90)
CLICK_MARK = (248, 188, 58)

VEHICLE_COLORS = {
    "car":        (70, 122, 255),
    "truck":      (231, 132, 66),
    "bus":        (201, 84, 84),
    "motorcycle": (167, 85, 206),
    "bicycle":    (58, 174, 110),
}
VEHICLE_DEFAULT = (100, 113, 138)
VEHICLE_OUTLINE = (33, 41, 56)
VEHICLE_GLASS = (223, 240, 251)

SIGN_STOP_RED = (198, 55, 52)
SIGN_YIELD_RED = (197, 75, 69)
SIGN_WHITE = (250, 250, 247)
SIGN_GOLD = (221, 178, 76)

ROAD_STYLE: Dict[str, Dict[str, Any]] = {
    "motorway": {
        "rank": 10,
        "fill": (243, 197, 118),
        "casing": (191, 152, 89),
        "border": (255, 245, 228),
        "label_zoom": 0.45,
        "label_px": 110,
    },
    "trunk": {
        "rank": 9,
        "fill": (245, 206, 136),
        "casing": (195, 161, 102),
        "border": (255, 247, 232),
        "label_zoom": 0.55,
        "label_px": 110,
    },
    "primary": {
        "rank": 8,
        "fill": (247, 221, 154),
        "casing": (199, 174, 121),
        "border": (255, 250, 238),
        "label_zoom": 0.7,
        "label_px": 120,
    },
    "secondary": {
        "rank": 7,
        "fill": (251, 247, 240),
        "casing": (191, 184, 172),
        "border": (255, 255, 252),
        "label_zoom": 0.95,
        "label_px": 120,
    },
    "tertiary": {
        "rank": 6,
        "fill": (250, 248, 242),
        "casing": (183, 178, 170),
        "border": (255, 255, 252),
        "label_zoom": 1.05,
        "label_px": 120,
    },
    "unclassified": {
        "rank": 5,
        "fill": (249, 247, 241),
        "casing": (179, 175, 168),
        "border": (255, 255, 252),
        "label_zoom": 1.2,
        "label_px": 130,
    },
    "residential": {
        "rank": 4,
        "fill": (248, 246, 241),
        "casing": (175, 171, 164),
        "border": (255, 255, 251),
        "label_zoom": 1.35,
        "label_px": 130,
    },
    "service": {
        "rank": 3,
        "fill": (242, 239, 232),
        "casing": (167, 162, 154),
        "border": (252, 251, 248),
        "label_zoom": 1.75,
        "label_px": 120,
    },
    "living_street": {
        "rank": 3,
        "fill": (243, 239, 234),
        "casing": (171, 164, 156),
        "border": (252, 251, 248),
        "label_zoom": 1.8,
        "label_px": 120,
    },
    "pedestrian": {
        "rank": 2,
        "fill": (234, 227, 208),
        "casing": (173, 161, 139),
        "border": (245, 239, 228),
        "label_zoom": 2.1,
        "label_px": 120,
    },
    "footway": {
        "rank": 1,
        "fill": (215, 230, 203),
        "casing": (151, 177, 135),
        "border": (236, 246, 229),
        "label_zoom": 2.45,
        "label_px": 120,
    },
    "cycleway": {
        "rank": 2,
        "fill": (193, 230, 204),
        "casing": (128, 173, 142),
        "border": (229, 247, 232),
        "label_zoom": 2.2,
        "label_px": 120,
    },
    "path": {
        "rank": 1,
        "fill": (203, 220, 191),
        "casing": (137, 160, 125),
        "border": (230, 240, 222),
        "label_zoom": 2.55,
        "label_px": 120,
    },
}
DEFAULT_ROAD_STYLE = {
    "rank": 4,
    "fill": (249, 247, 241),
    "casing": (178, 173, 166),
    "border": (255, 255, 252),
    "label_zoom": 1.3,
    "label_px": 130,
}

DETAIL_TYPES = {"service", "living_street", "pedestrian", "footway", "cycleway", "path"}

PAN_SPEED = 16.0
ZOOM_STEP = 1.12
ZOOM_MIN = 0.03
ZOOM_MAX = 120.0

LANE_ARROW_ZOOM_MIN = 1.05
LANE_BORDER_ZOOM_MIN = 1.55
ROAD_LABEL_ZOOM_MIN = 0.7
SIGN_ZOOM_MIN = 1.45
SIGN_TEXT_ZOOM_MIN = 2.2
SIGNAL_ZOOM_MIN = 1.0
VEHICLE_DETAIL_ZOOM_MIN = 0.6


def _road_style(hw: str) -> Dict[str, Any]:
    return ROAD_STYLE.get(hw, DEFAULT_ROAD_STYLE)


def _polyline_length(points: List[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def _point_and_angle_at_distance(points: List[Tuple[float, float]],
                                 distance: float) -> Tuple[Tuple[float, float], float]:
    if not points:
        return (0.0, 0.0), 0.0
    if len(points) == 1:
        return points[0], 0.0

    remaining = max(0.0, distance)
    for a, b in zip(points, points[1:]):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-6:
            continue
        if remaining <= seg_len:
            t = remaining / seg_len
            return ((a[0] + dx * t, a[1] + dy * t),
                    math.degrees(math.atan2(dy, dx)))
        remaining -= seg_len

    a, b = points[-2], points[-1]
    return b, math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _upright_angle(angle_deg: float) -> float:
    angle = angle_deg
    if angle > 90.0:
        angle -= 180.0
    elif angle < -90.0:
        angle += 180.0
    return angle


def _rotated_box(center: Tuple[float, float], length_px: float,
                 width_px: float, angle_rad: float) -> List[Tuple[float, float]]:
    cx, cy = center
    dx = math.cos(angle_rad)
    dy = math.sin(angle_rad)
    px = -dy
    py = dx
    hl = length_px * 0.5
    hw = width_px * 0.5
    return [
        (cx - dx * hl - px * hw, cy - dy * hl - py * hw),
        (cx + dx * hl - px * hw, cy + dy * hl - py * hw),
        (cx + dx * hl + px * hw, cy + dy * hl + py * hw),
        (cx - dx * hl + px * hw, cy - dy * hl + py * hw),
    ]


def _signal_color(state_char: str) -> Tuple[int, int, int]:
    c = (state_char or "r")[0]
    if c in ("G", "g"):
        return (76, 200, 92)
    if c in ("Y", "y", "u"):
        return (249, 201, 63)
    if c in ("o", "O"):
        return (123, 131, 147)
    return (220, 76, 71)


def _darken(color: Tuple[int, int, int], factor: float = 0.75) -> Tuple[int, int, int]:
    return tuple(max(0, min(255, int(channel * factor))) for channel in color)


def _lane_is_vehicle_usable(lane: Dict[str, Any], highway_type: str) -> bool:
    if highway_type in {"pedestrian", "footway"}:
        return False
    allow = lane.get("allow", "") or ""
    if allow:
        allowed = set(allow.split())
        return bool({"passenger", "truck", "bus", "motorcycle", "bicycle"} & allowed)
    return True


class Button:
    def __init__(self, rect: Tuple[int, int, int, int], label: str,
                 color: Tuple[int, int, int] = BTN,
                 active_color: Tuple[int, int, int] = BTN_ACTIVE):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.color = color
        self.active_color = active_color
        self.active = False
        self.enabled = True

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        hovered = self.rect.collidepoint(pygame.mouse.get_pos())
        if not self.enabled:
            col = BTN_DISABLED
        elif self.active:
            col = self.active_color
        elif hovered:
            col = BTN_HOVER
        else:
            col = self.color
        pygame.draw.rect(surf, col, self.rect, border_radius=8)
        pygame.draw.rect(surf, PANEL_BORDER, self.rect, 1, border_radius=8)
        text_col = TEXT if self.enabled else TEXT_DIM
        txt = font.render(self.label, True, text_col)
        surf.blit(txt, txt.get_rect(center=self.rect.center))

    def hit(self, pos: Tuple[int, int]) -> bool:
        return self.enabled and self.rect.collidepoint(pos)


class SumoRenderer:
    def __init__(self, G: nx.DiGraph,
                 data_dir: str = "data",
                 osm_cache_path: Optional[str] = None):
        if G.number_of_nodes() == 0:
            raise ValueError("Grafo vuoto.")

        self.G = G
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        self._window_w = INITIAL_WINDOW_W
        self._window_h = INITIAL_WINDOW_H
        self._map_w = self._window_w - PANEL_W

        pygame.init()
        self._screen = pygame.display.set_mode((self._window_w, self._window_h), pygame.RESIZABLE)
        pygame.display.set_caption("CityGraph + SUMO - Simulazione Interattiva")
        self._clock = pygame.time.Clock()

        self._font_ui_lg = pygame.font.SysFont("Bahnschrift", 18, bold=True)
        self._font_ui_md = pygame.font.SysFont("Segoe UI", 14)
        self._font_ui_sm = pygame.font.SysFont("Segoe UI", 12)
        self._font_road_md = pygame.font.SysFont("Trebuchet MS", 13, bold=True)
        self._font_road_sm = pygame.font.SysFont("Trebuchet MS", 11)

        log.info("Conversione grafo -> .net.xml (%d nodi, %d archi)...",
                 G.number_of_nodes(), G.number_of_edges())
        self.exporter = SumoNetExporter(G, osm_cache_path=osm_cache_path)
        self.net_path = os.path.join(data_dir, "city.net.xml")
        self.exporter.export(self.net_path)

        self._lane_draw_list = self._precompute_lane_draw_list()
        self._road_label_list = self._precompute_road_labels()
        self._junctions = self.exporter.get_junctions()
        self._sign_markers = self.exporter.get_sign_markers()
        log.info("Renderer SUMO: %d lane, %d edge label, %d marker STOP/YIELD",
                 len(self._lane_draw_list), len(self._road_label_list), len(self._sign_markers))

        bounds = self.exporter.get_bounds()
        if not bounds:
            raise ValueError("SumoNetExporter non ha prodotto geometria SUMO valida.")
        self._sumo_xmin, self._sumo_ymin, self._sumo_xmax, self._sumo_ymax = bounds

        self._cam_zoom = 1.0
        self._cam_off_x = 0.0
        self._cam_off_y = 0.0
        self._fit_camera()

        self.scenario: Optional[ScenarioBuilder] = None
        self.sim = None

        self._sim_running = False
        self._paused = False
        self._veh_type = "car"
        self._spawn_count = 10
        self._last_click_sumo: Optional[Tuple[float, float]] = None
        self._vehicles: List[Dict[str, Any]] = []
        self._signal_states: List[Dict[str, Any]] = []
        self._stats: Dict[str, Any] = {}
        self._log_lines: List[str] = []
        self._bg: Optional[pygame.Surface] = None

        self._build_buttons()

    def _precompute_lane_draw_list(self) -> List[Dict[str, Any]]:
        edge_by_id = {edge["id"]: edge for edge in self.exporter.get_draw_edges()}
        lanes: List[Dict[str, Any]] = []

        for lane in self.exporter.get_draw_lanes():
            edge = edge_by_id.get(lane["edge_id"])
            if not edge:
                continue
            hw = edge.get("hw", "") or ""
            style = _road_style(hw)
            label = edge.get("name", "") or edge.get("ref", "") or ""
            lanes.append({
                "id": lane["id"],
                "edge_id": lane["edge_id"],
                "hw": hw,
                "shape": lane["shape"],
                "width_m": float(lane.get("width", 3.2) or 3.2),
                "name": edge.get("name", "") or "",
                "label": label,
                "rank": style["rank"],
                "fill": style["fill"],
                "casing": style["casing"],
                "border": style["border"],
                "detail": hw in DETAIL_TYPES,
                "vehicular": _lane_is_vehicle_usable(lane, hw),
            })

        lanes.sort(key=lambda item: item["rank"])
        return lanes

    def _precompute_road_labels(self) -> List[Dict[str, Any]]:
        labels: List[Dict[str, Any]] = []
        for edge in self.exporter.get_draw_edges():
            label = edge.get("name", "") or edge.get("ref", "") or ""
            if not label:
                continue
            labels.append({
                "id": edge["id"],
                "label": label,
                "hw": edge.get("hw", "") or "",
                "shape": edge["shape"],
            })
        return labels

    def _build_buttons(self) -> None:
        px = self._map_w + 14
        bw = PANEL_W - 28
        bh = 36

        self._btn_start = Button((px, 64, bw, bh), "Avvia Simulazione")
        self._btn_stop = Button((px, 106, bw, bh), "Ferma Simulazione",
                                color=BTN_STOP, active_color=BTN_STOP)
        self._btn_stop.enabled = False
        self._btn_pause = Button((px, 148, bw, bh), "Pausa / Riprendi")
        self._btn_pause.enabled = False

        self._vtype_btns: Dict[str, Button] = {}
        for idx, vt in enumerate(VEHICLE_TYPES.keys()):
            btn = Button((px, 224 + idx * 40, bw, bh), vt.capitalize())
            self._vtype_btns[vt] = btn
        self._vtype_btns["car"].active = True

        last_y = 224 + len(VEHICLE_TYPES) * 40
        self._btn_spawn_random = Button((px, last_y + 16, bw, bh),
                                        f"Spawn {self._spawn_count} random")
        self._btn_spawn_random.enabled = False

        half = (bw - 10) // 2
        self._btn_spawn_minus = Button((px, last_y + 58, half, 28), "- meno")
        self._btn_spawn_plus = Button((px + half + 10, last_y + 58, half, 28), "+ piu")
        self._btn_reset_cam = Button((px, self._window_h - 74, bw, bh), "Reset Camera")

        self._all_buttons = [
            self._btn_start, self._btn_stop, self._btn_pause,
            *self._vtype_btns.values(),
            self._btn_spawn_random, self._btn_spawn_minus,
            self._btn_spawn_plus, self._btn_reset_cam,
        ]

    def run(self) -> None:
        self._build_background()
        running = True

        while running:
            if self._sim_running and not self._paused and self.sim:
                self.sim.step()
                self._vehicles = self.sim.get_vehicle_positions()
                self._signal_states = self.sim.get_signal_states()
                self._stats = self.sim.get_stats()

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_SPACE:
                        self._toggle_pause()
                    elif event.key == pygame.K_r:
                        self._fit_camera()
                        self._build_background()
                elif event.type == pygame.VIDEORESIZE:
                    self._resize(event.w, event.h)
                elif event.type == pygame.MOUSEWHEEL:
                    mx, my = pygame.mouse.get_pos()
                    if mx < self._map_w:
                        factor = ZOOM_STEP if event.y > 0 else 1.0 / ZOOM_STEP
                        self._zoom_at(mx, my, factor)
                        self._build_background()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._on_click(event.pos)

            keys = pygame.key.get_pressed()
            moved = False
            speed = PAN_SPEED / max(self._cam_zoom, 0.001)
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                self._cam_off_x += speed
                moved = True
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                self._cam_off_x -= speed
                moved = True
            if keys[pygame.K_UP] or keys[pygame.K_w]:
                self._cam_off_y += speed
                moved = True
            if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                self._cam_off_y -= speed
                moved = True
            if moved:
                self._build_background()

            self._draw()
            pygame.display.flip()
            self._clock.tick(60)

        self._cleanup()
        pygame.quit()

    def _resize(self, width: int, height: int) -> None:
        self._window_w = max(1260, int(width))
        self._window_h = max(760, int(height))
        self._map_w = self._window_w - PANEL_W
        self._screen = pygame.display.set_mode((self._window_w, self._window_h), pygame.RESIZABLE)
        self._build_buttons()
        self._build_background()

    def _on_click(self, pos: Tuple[int, int]) -> None:
        mx, my = pos
        if mx >= self._map_w:
            if self._btn_start.hit(pos):
                self._start_simulation()
            elif self._btn_stop.hit(pos):
                self._stop_simulation()
            elif self._btn_pause.hit(pos):
                self._toggle_pause()
            elif self._btn_spawn_random.hit(pos):
                self._do_spawn_random()
            elif self._btn_spawn_minus.hit(pos):
                self._spawn_count = max(1, self._spawn_count - 5)
                self._btn_spawn_random.label = f"Spawn {self._spawn_count} random"
            elif self._btn_spawn_plus.hit(pos):
                self._spawn_count = min(200, self._spawn_count + 5)
                self._btn_spawn_random.label = f"Spawn {self._spawn_count} random"
            elif self._btn_reset_cam.hit(pos):
                self._fit_camera()
                self._build_background()
            else:
                for vt, btn in self._vtype_btns.items():
                    if btn.hit(pos):
                        self._veh_type = vt
                        for item in self._vtype_btns.values():
                            item.active = False
                        btn.active = True
                        break
            return

        sx, sy = self._screen_to_sumo(mx, my)
        self._last_click_sumo = (sx, sy)
        if self._sim_running and self.sim:
            vid = self.sim.spawn_at_xy(sx, sy, vtype=self._veh_type)
            self._log(f"Spawn {vid} ({self._veh_type})" if vid
                      else "Spawn fallito - clicca su una corsia valida")
        else:
            self._log(f"Sim non attiva. Click a ({sx:.0f}, {sy:.0f})")

    def _start_simulation(self) -> None:
        if self._sim_running:
            return
        self._log("Avvio in corso...")
        try:
            from sumo.simulation_controller import SimulationController
            self.scenario = ScenarioBuilder(self.net_path)
            cfg = self.scenario.build()
            self.sim = SimulationController(cfg, self.exporter, self._veh_type)
            self.sim.start()
            self._sim_running = True
            self._paused = False
            self._vehicles = self.sim.get_vehicle_positions()
            self._signal_states = self.sim.get_signal_states()
            self._btn_start.enabled = False
            self._btn_stop.enabled = True
            self._btn_pause.enabled = True
            self._btn_spawn_random.enabled = True
            self._log("Simulazione avviata.")
        except Exception as exc:
            log.error("Errore avvio SUMO: %s", exc, exc_info=True)
            self._log(f"ERRORE: {exc}")

    def _stop_simulation(self) -> None:
        if not self._sim_running:
            return
        if self.sim:
            self.sim.stop()
        if self.scenario:
            self.scenario.cleanup()
        self._sim_running = False
        self._paused = False
        self._vehicles = []
        self._signal_states = []
        self._stats = {}
        self._btn_start.enabled = True
        self._btn_stop.enabled = False
        self._btn_pause.enabled = False
        self._btn_spawn_random.enabled = False
        self._log("Simulazione fermata.")

    def _toggle_pause(self) -> None:
        if not self._sim_running:
            return
        self._paused = not self._paused
        self._btn_pause.active = self._paused
        self._log("Pausa." if self._paused else "Ripreso.")

    def _do_spawn_random(self) -> None:
        if not self._sim_running or not self.sim:
            return
        created = self.sim.spawn_random(self._spawn_count, self._veh_type)
        self._log(f"Spawned {len(created)}/{self._spawn_count} random")

    def _fit_camera(self) -> None:
        span_x = self._sumo_xmax - self._sumo_xmin
        span_y = self._sumo_ymax - self._sumo_ymin
        if span_x < 1 or span_y < 1:
            self._cam_zoom = 1.0
            self._cam_off_x = 0.0
            self._cam_off_y = 0.0
            return
        pad = 0.05
        zoom_x = self._map_w * (1 - 2 * pad) / span_x
        zoom_y = self._window_h * (1 - 2 * pad) / span_y
        self._cam_zoom = min(zoom_x, zoom_y)
        cx = (self._sumo_xmin + self._sumo_xmax) * 0.5
        cy = (self._sumo_ymin + self._sumo_ymax) * 0.5
        self._cam_off_x = self._map_w * 0.5 - cx * self._cam_zoom
        self._cam_off_y = self._window_h * 0.5 + cy * self._cam_zoom

    def _zoom_at(self, px: int, py: int, factor: float) -> None:
        new_zoom = max(ZOOM_MIN, min(ZOOM_MAX, self._cam_zoom * factor))
        ratio = new_zoom / self._cam_zoom
        self._cam_off_x = px - ratio * (px - self._cam_off_x)
        self._cam_off_y = py - ratio * (py - self._cam_off_y)
        self._cam_zoom = new_zoom

    def _sumo_to_screen(self, sx: float, sy: float) -> Tuple[int, int]:
        return (
            int(sx * self._cam_zoom + self._cam_off_x),
            int(-sy * self._cam_zoom + self._cam_off_y),
        )

    def _screen_to_sumo(self, px: int, py: int) -> Tuple[float, float]:
        return (
            (px - self._cam_off_x) / self._cam_zoom,
            -(py - self._cam_off_y) / self._cam_zoom,
        )

    def _on_map(self, px: int, py: int, margin: int = 0) -> bool:
        return -margin <= px <= self._map_w + margin and -margin <= py <= self._window_h + margin

    def _build_background(self) -> None:
        surf = pygame.Surface((self._map_w, self._window_h))
        surf.fill(MAP_BG)
        self._draw_map_backdrop(surf)
        self._draw_lane_layer(surf)
        self._draw_static_signal_hubs(surf)
        self._draw_road_labels(surf)
        self._draw_sign_markers(surf)
        self._bg = surf

    def _draw_map_backdrop(self, surf: pygame.Surface) -> None:
        for x in range(0, self._map_w, 96):
            pygame.draw.line(surf, MAP_GRID, (x, 0), (x, self._window_h), 1)
        for y in range(0, self._window_h, 96):
            pygame.draw.line(surf, MAP_GRID, (0, y), (self._map_w, y), 1)

        cx = int(self._map_w * 0.72)
        cy = int(self._window_h * 0.18)
        pygame.draw.circle(surf, MAP_WATERMARK, (cx, cy), max(140, int(self._cam_zoom * 180)))
        pygame.draw.circle(surf, MAP_WATERMARK, (int(self._map_w * 0.22), int(self._window_h * 0.82)),
                           max(100, int(self._cam_zoom * 130)))

    def _draw_lane_layer(self, surf: pygame.Surface) -> None:
        zoom = self._cam_zoom
        show_detail = zoom >= 0.95

        for lane in self._lane_draw_list:
            if lane["detail"] and not show_detail:
                continue
            pts = [self._sumo_to_screen(x, y) for x, y in lane["shape"]]
            if len(pts) < 2 or not any(self._on_map(px, py, 32) for px, py in pts):
                continue

            width_px = max(1, min(28, int(round(lane["width_m"] * zoom * 0.98))))
            if lane["hw"] in {"footway", "cycleway", "path", "pedestrian"}:
                width_px = max(1, min(18, int(round(lane["width_m"] * zoom * 0.92))))

            casing_w = min(34, width_px + (4 if width_px >= 6 else 2))
            pygame.draw.lines(surf, lane["casing"], False, pts, casing_w)
            pygame.draw.lines(surf, lane["fill"], False, pts, width_px)

            if zoom >= LANE_BORDER_ZOOM_MIN and width_px >= 5:
                pygame.draw.lines(surf, lane["border"], False, pts, 1)

            if zoom >= LANE_ARROW_ZOOM_MIN and width_px >= 4 and lane["vehicular"]:
                self._draw_lane_arrows(surf, pts, width_px)

    def _draw_lane_arrows(self, surf: pygame.Surface,
                          pts: List[Tuple[int, int]], width_px: int) -> None:
        total = _polyline_length(pts)
        if total < 80:
            return

        spacing = max(90.0, min(190.0, total / max(1.0, total / 135.0)))
        arrow_len = max(10.0, min(24.0, width_px * 2.2))
        arrow_w = max(4.0, arrow_len * 0.42)
        color = (110, 118, 132)

        d = min(30.0, total * 0.3)
        while d <= total - 18.0:
            center, angle_deg = _point_and_angle_at_distance(pts, d)
            ang = math.radians(angle_deg)
            dx = math.cos(ang)
            dy = math.sin(ang)
            px = -dy
            py = dx

            tip = (center[0] + dx * arrow_len * 0.5, center[1] + dy * arrow_len * 0.5)
            tail = (center[0] - dx * arrow_len * 0.5, center[1] - dy * arrow_len * 0.5)
            left = (tail[0] + px * arrow_w * 0.5, tail[1] + py * arrow_w * 0.5)
            right = (tail[0] - px * arrow_w * 0.5, tail[1] - py * arrow_w * 0.5)
            pygame.draw.polygon(surf, color, [tip, left, right])
            d += spacing

    def _draw_road_labels(self, surf: pygame.Surface) -> None:
        zoom = self._cam_zoom
        if zoom < ROAD_LABEL_ZOOM_MIN:
            return

        placed: List[pygame.Rect] = []
        slots = set()

        for edge in self._road_label_list:
            style = _road_style(edge["hw"])
            if zoom < style["label_zoom"]:
                continue

            pts = [self._sumo_to_screen(x, y) for x, y in edge["shape"]]
            total = _polyline_length(pts)
            if total < style["label_px"]:
                continue
            if not any(self._on_map(px, py, 40) for px, py in pts):
                continue

            center, angle_deg = _point_and_angle_at_distance(pts, total * 0.5)
            if not self._on_map(int(center[0]), int(center[1]), 20):
                continue

            slot = (edge["label"], int(center[0] // 180), int(center[1] // 120))
            if slot in slots:
                continue

            angle = _upright_angle(angle_deg)
            font = self._font_road_md if zoom >= 1.55 else self._font_road_sm
            txt = font.render(edge["label"], True, TEXT_DARK)
            bg = pygame.Surface((txt.get_width() + 10, txt.get_height() + 4), pygame.SRCALPHA)
            bg.fill((255, 255, 255, 180))
            bg.blit(txt, (5, 2))
            rotated = pygame.transform.rotate(bg, -angle)
            rect = rotated.get_rect(center=(int(center[0]), int(center[1])))
            if rect.left < 0 or rect.right > self._map_w or rect.top < 0 or rect.bottom > self._window_h:
                continue
            if any(rect.colliderect(other.inflate(16, 10)) for other in placed):
                continue

            surf.blit(rotated, rect)
            placed.append(rect)
            slots.add(slot)

    def _draw_sign_markers(self, surf: pygame.Surface) -> None:
        if self._cam_zoom < SIGN_ZOOM_MIN:
            return

        radius = max(7, min(18, int(5 + self._cam_zoom * 2.2)))
        for sign in self._sign_markers:
            px, py = self._sumo_to_screen(sign["x"], sign["y"])
            if not self._on_map(px, py, 24):
                continue
            if sign["kind"] == "stop":
                self._draw_stop_sign(surf, (px, py), radius)
            elif sign["kind"] == "give_way":
                self._draw_yield_sign(surf, (px, py), radius)

    def _draw_stop_sign(self, surf: pygame.Surface,
                        center: Tuple[int, int], radius: int) -> None:
        pts = []
        for idx in range(8):
            ang = math.radians(22.5 + idx * 45.0)
            pts.append((
                center[0] + math.cos(ang) * radius,
                center[1] + math.sin(ang) * radius,
            ))
        pygame.draw.polygon(surf, SIGN_STOP_RED, pts)
        pygame.draw.polygon(surf, SIGN_WHITE, pts, 2)
        if self._cam_zoom >= SIGN_TEXT_ZOOM_MIN and radius >= 10:
            txt = self._font_ui_sm.render("STOP", True, SIGN_WHITE)
            surf.blit(txt, txt.get_rect(center=center))

    def _draw_yield_sign(self, surf: pygame.Surface,
                         center: Tuple[int, int], radius: int) -> None:
        pts = [
            (center[0], center[1] + radius),
            (center[0] - radius, center[1] - radius * 0.75),
            (center[0] + radius, center[1] - radius * 0.75),
        ]
        pygame.draw.polygon(surf, SIGN_WHITE, pts)
        pygame.draw.polygon(surf, SIGN_YIELD_RED, pts, 3)
        if self._cam_zoom >= SIGN_TEXT_ZOOM_MIN and radius >= 11:
            txt = self._font_ui_sm.render("Y", True, SIGN_YIELD_RED)
            surf.blit(txt, txt.get_rect(center=(center[0], center[1] - 1)))

    def _draw_static_signal_hubs(self, surf: pygame.Surface) -> None:
        if self._cam_zoom < SIGNAL_ZOOM_MIN:
            return
        for junction in self._junctions:
            if junction.get("type") != "traffic_light":
                continue
            px, py = self._sumo_to_screen(junction["x"], junction["y"])
            if not self._on_map(px, py, 20):
                continue
            pygame.draw.circle(surf, (54, 60, 74), (px, py), 6)
            pygame.draw.circle(surf, (210, 212, 218), (px, py), 2)

    def _draw_signal_overlay(self, screen: pygame.Surface) -> None:
        if self._cam_zoom < SIGNAL_ZOOM_MIN:
            return

        if not self._signal_states:
            return

        for signal in self._signal_states:
            for lane_id, state_char in signal.get("lane_states", {}).items():
                lane = self.exporter.get_lane(lane_id)
                if not lane:
                    continue
                pts = [self._sumo_to_screen(x, y) for x, y in lane["shape"]]
                if len(pts) < 2:
                    continue

                total = _polyline_length(pts)
                anchor, angle_deg = _point_and_angle_at_distance(pts, max(0.0, total - 12.0))
                if not self._on_map(int(anchor[0]), int(anchor[1]), 16):
                    continue

                radius = max(4, min(10, int(3 + self._cam_zoom * 1.1)))
                color = _signal_color(state_char)
                pygame.draw.circle(screen, (28, 31, 37), (int(anchor[0]), int(anchor[1])), radius + 2)
                pygame.draw.circle(screen, color, (int(anchor[0]), int(anchor[1])), radius)

                if self._cam_zoom >= 2.0:
                    ang = math.radians(angle_deg)
                    dx = math.cos(ang)
                    dy = math.sin(ang)
                    tail = (anchor[0] - dx * (radius + 8), anchor[1] - dy * (radius + 8))
                    pygame.draw.line(screen, (70, 77, 89), (int(tail[0]), int(tail[1])),
                                     (int(anchor[0]), int(anchor[1])), 2)

    def _draw_vehicles(self, screen: pygame.Surface) -> None:
        zoom = self._cam_zoom
        for vehicle in self._vehicles:
            px, py = self._sumo_to_screen(vehicle["x"], vehicle["y"])
            if not self._on_map(px, py, 24):
                continue

            length_px = max(6.0, min(54.0, float(vehicle.get("length", 4.5) or 4.5) * zoom * 0.92))
            width_px = max(3.0, min(22.0, float(vehicle.get("width", 1.8) or 1.8) * zoom * 0.96))

            if zoom < VEHICLE_DETAIL_ZOOM_MIN:
                length_px = max(5.0, length_px)
                width_px = max(3.0, width_px)

            angle_rad = math.radians(float(vehicle.get("angle", 90.0) or 90.0) - 90.0)
            body = _rotated_box((px, py), length_px, width_px, angle_rad)
            shadow = [(x + 1.0, y + 2.0) for x, y in body]
            color = VEHICLE_COLORS.get(vehicle.get("type", ""), VEHICLE_DEFAULT)

            pygame.draw.polygon(screen, (0, 0, 0, 50), shadow)
            pygame.draw.polygon(screen, _darken(color, 0.82), body)
            pygame.draw.polygon(screen, VEHICLE_OUTLINE, body, 1)

            dx = math.cos(angle_rad)
            dy = math.sin(angle_rad)
            pxn = -dy
            pyn = dx
            nose_center = (px + dx * length_px * 0.16, py + dy * length_px * 0.16)
            glass_w = width_px * 0.45
            glass_a = (nose_center[0] - pxn * glass_w * 0.5, nose_center[1] - pyn * glass_w * 0.5)
            glass_b = (nose_center[0] + pxn * glass_w * 0.5, nose_center[1] + pyn * glass_w * 0.5)
            pygame.draw.line(screen, VEHICLE_GLASS, (int(glass_a[0]), int(glass_a[1])),
                             (int(glass_b[0]), int(glass_b[1])), max(1, int(width_px * 0.22)))

    def _draw(self) -> None:
        screen = self._screen
        if self._bg:
            screen.blit(self._bg, (0, 0))
        else:
            pygame.draw.rect(screen, MAP_BG, (0, 0, self._map_w, self._window_h))

        self._draw_signal_overlay(screen)
        self._draw_vehicles(screen)

        if self._last_click_sumo:
            cx, cy = self._sumo_to_screen(*self._last_click_sumo)
            if self._on_map(cx, cy, 5):
                pygame.draw.circle(screen, CLICK_MARK, (cx, cy), 9, 2)
                pygame.draw.line(screen, CLICK_MARK, (cx - 12, cy), (cx + 12, cy), 1)
                pygame.draw.line(screen, CLICK_MARK, (cx, cy - 12), (cx, cy + 12), 1)

        self._draw_panel(screen)
        self._draw_map_hud(screen)

    def _draw_panel(self, screen: pygame.Surface) -> None:
        pygame.draw.rect(screen, PANEL_BG, (self._map_w, 0, PANEL_W, self._window_h))
        pygame.draw.line(screen, PANEL_BORDER, (self._map_w, 0), (self._map_w, self._window_h), 2)

        px0 = self._map_w + 14
        self._text(screen, "SUMO + Neo4j", (px0, 14), self._font_ui_lg, TEXT)
        self._text(screen, "Simulazione Interattiva", (px0, 36), self._font_ui_sm, TEXT_DIM)

        for btn in self._all_buttons:
            btn.draw(screen, self._font_ui_md)

        self._text(screen, "Tipo veicolo:", (px0, 202), self._font_ui_sm, TEXT_DIM)

        stats_y = 224 + len(VEHICLE_TYPES) * 40 + 108
        self._text(screen, "Statistiche", (px0, stats_y), self._font_ui_md, TEXT_DIM)
        stats_y += 22

        if self._sim_running and self._stats:
            lines = [
                f"Tempo sim : {self._stats.get('time', 0):.1f} s",
                f"Veicoli   : {self._stats.get('vehicles_active', 0)}",
                f"Totale sp.: {self._stats.get('vehicles_total', 0)}",
                f"Teleport  : {self._stats.get('teleports', 0)}",
                f"Collisioni: {self._stats.get('collisions', 0)}",
                f"Semafori  : {len(self._signal_states)}",
            ]
        else:
            state = "PAUSA" if self._paused else ("IN CORSO" if self._sim_running else "FERMA")
            lines = [
                f"Stato     : {state}",
                f"Nodi      : {self.G.number_of_nodes()}",
                f"Archi     : {self.G.number_of_edges()}",
                f"Lane      : {len(self._lane_draw_list)}",
                f"Segnali   : {len(self._sign_markers)}",
                f"Zoom      : {self._cam_zoom:.3f}",
            ]
        for line in lines:
            self._text(screen, line, (px0, stats_y), self._font_ui_sm, TEXT)
            stats_y += 18

        legend_y = stats_y + 12
        self._text(screen, "Legenda", (px0, legend_y), self._font_ui_md, TEXT_DIM)
        legend_y += 22
        self._text(screen, "Frecce: senso di marcia", (px0, legend_y), self._font_ui_sm, TEXT)
        legend_y += 16
        self._text(screen, "STOP / precedenza: da OSM", (px0, legend_y), self._font_ui_sm, TEXT)
        legend_y += 16
        self._text(screen, "Semafori: stato live da SUMO", (px0, legend_y), self._font_ui_sm, TEXT)

        log_y = self._window_h - 220
        self._text(screen, "Log", (px0, log_y), self._font_ui_md, TEXT_DIM)
        log_y += 18
        for line in self._log_lines[-8:]:
            self._text(screen, line[:36], (px0, log_y), self._font_ui_sm, TEXT_DIM)
            log_y += 15

        fps = self._clock.get_fps()
        self._text(screen, f"FPS:{fps:.0f}  Zoom:{self._cam_zoom:.3f}",
                   (px0, self._window_h - 22), self._font_ui_sm, TEXT_DIM)

    def _draw_map_hud(self, screen: pygame.Surface) -> None:
        lines = [
            "WASD/Frecce=Pan  Scroll=Zoom  R=Reset  ESC=Esci",
            "Click sulla mappa: spawn veicolo sulla corsia piu vicina",
        ]
        if self._paused:
            lines.insert(0, "PAUSA - premi Spazio per riprendere")

        y = 8
        for line in lines:
            txt = self._font_ui_sm.render(line, True, TEXT_DARK)
            bg = pygame.Surface((txt.get_width() + 10, txt.get_height() + 6), pygame.SRCALPHA)
            bg.fill((255, 255, 255, 175))
            screen.blit(bg, (6, y - 3))
            screen.blit(txt, (11, y))
            y += txt.get_height() + 6

    @staticmethod
    def _text(surf: pygame.Surface, text: str, pos: Tuple[int, int],
              font: pygame.font.Font, color: Tuple[int, int, int]) -> None:
        surf.blit(font.render(text, True, color), pos)

    def _log(self, msg: str) -> None:
        log.info("[SumoRenderer] %s", msg)
        self._log_lines.append(msg)
        if len(self._log_lines) > 60:
            self._log_lines = self._log_lines[-60:]

    def _cleanup(self) -> None:
        if self._sim_running:
            self._stop_simulation()
