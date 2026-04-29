"""
sumo_renderer.py — Renderer interattivo Pygame che integra:
  - Visualizzazione del grafo stradale (da Neo4j/NetworkX)
  - Simulazione del traffico in tempo reale (libsumo o traci)
  - UI con pannello laterale: bottoni, stats, selezione tipo veicolo
  - Click per spawn veicolo nel punto cliccato sulla mappa
  - Zoom centrato sul mouse, pan WASD/frecce

Flusso:
  1. SumoNetExporter converte il grafo in .net.xml (via netconvert o fallback interno)
  2. ScenarioBuilder crea .rou.xml + .sumocfg in dir temporanea
  3. Click "Avvia Simulazione" → traci.start() lazy (non al caricamento del modulo)
  4. Click sulla mappa → spawn veicolo nel punto cliccato
  5. Click "Spawn Random N" → spawn casuale su tutta la rete
  6. [Spazio] → pausa/riprendi
"""

import os
import logging
from typing import Optional, List, Dict, Tuple

import pygame
import networkx as nx

from sumo.net_exporter import SumoNetExporter
from sumo.scenario_builder import ScenarioBuilder, VEHICLE_TYPES

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# COSTANTI UI
# ─────────────────────────────────────────────
WINDOW_W  = 1400
WINDOW_H  = 900
PANEL_W   = 260
MAP_W     = WINDOW_W - PANEL_W

C_BG       = (15,  17,  26)
C_PANEL    = (22,  26,  40)
C_PANEL_BD = (40,  50,  75)
C_ROAD     = (80,  95, 120)
C_VEH      = {
    "car":        (255,  80,  80),
    "truck":      (255, 160,  40),
    "bus":        ( 80, 140, 255),
    "motorcycle": (200,  80, 220),
    "bicycle":    ( 80, 210, 120),
}
C_VEH_DEF  = (200, 200, 200)
C_BTN      = ( 40,  55,  90)
C_BTN_HOV  = ( 60,  85, 140)
C_BTN_ACT  = ( 30, 180, 100)
C_BTN_STOP = (180,  50,  50)
C_TEXT     = (210, 215, 235)
C_TEXT_DIM = (120, 130, 155)
C_SPAWN_MK = (255, 220,  60)
C_WHITE    = (255, 255, 255)

HW_COLOR: Dict[str, Tuple] = {
    "motorway":      (255, 165,  50),
    "trunk":         (255, 185,  80),
    "primary":       (240, 210, 100),
    "secondary":     (180, 185, 120),
    "tertiary":      (140, 155, 140),
    "unclassified":  (100, 115, 130),
    "residential":   ( 90, 110, 140),
    "service":       ( 70,  85, 105),
    "living_street": ( 80, 100, 120),
    "pedestrian":    (120, 140, 120),
    "footway":       ( 80, 130,  80),
    "cycleway":      ( 60, 160,  90),
    "path":          ( 70, 120,  70),
}
HW_WIDTH: Dict[str, float] = {
    "motorway": 5.0, "trunk": 4.5, "primary": 4.0,
    "secondary": 3.0, "tertiary": 2.5, "unclassified": 2.0,
    "residential": 1.8, "service": 1.4, "living_street": 1.6,
    "pedestrian": 1.2, "footway": 1.0, "cycleway": 1.0, "path": 0.8,
}

# Strade di dettaglio: visibili solo da zoom >= LOD_DETAIL_ZOOM_MIN
LOD_DETAIL_ONLY    = {"service", "living_street", "pedestrian", "footway", "cycleway", "path"}
LOD_DETAIL_ZOOM_MIN = 1.0

PAN_SPEED = 14.0
ZOOM_STEP = 1.13
ZOOM_MIN  = 0.01
ZOOM_MAX  = 100.0


# ─────────────────────────────────────────────
# BOTTONE
# ─────────────────────────────────────────────

class Button:
    def __init__(self, rect: Tuple, label: str,
                 color=C_BTN, active_color=C_BTN_ACT):
        self.rect         = pygame.Rect(rect)
        self.label        = label
        self.color        = color
        self.active_color = active_color
        self.active       = False
        self.enabled      = True

    def draw(self, surf: pygame.Surface, font: pygame.font.Font) -> None:
        hovered = self.rect.collidepoint(pygame.mouse.get_pos())
        if not self.enabled:
            col = (50, 55, 70)
        elif self.active:
            col = self.active_color
        elif hovered:
            col = C_BTN_HOV
        else:
            col = self.color
        pygame.draw.rect(surf, col, self.rect, border_radius=6)
        pygame.draw.rect(surf, C_PANEL_BD, self.rect, 1, border_radius=6)
        tc  = C_TEXT if self.enabled else C_TEXT_DIM
        txt = font.render(self.label, True, tc)
        surf.blit(txt, txt.get_rect(center=self.rect.center))

    def hit(self, pos: Tuple) -> bool:
        return self.enabled and self.rect.collidepoint(pos)


# ─────────────────────────────────────────────
# RENDERER
# ─────────────────────────────────────────────

class SumoRenderer:
    """
    Renderer interattivo Pygame per la simulazione SUMO integrata con Neo4j.

    Parametri:
      G              — grafo NetworkX da Neo4j
      data_dir       — directory dove salvare .net.xml
      osm_cache_path — (opzionale) percorso JSON Overpass per netconvert
    """

    def __init__(self, G: nx.DiGraph,
                 data_dir: str = "data",
                 osm_cache_path: Optional[str] = None):
        if G.number_of_nodes() == 0:
            raise ValueError("Grafo vuoto.")

        self.G        = G
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

        # ── Pygame — inizializzare PRIMA di qualsiasi Surface ─────────────────
        pygame.init()
        self._screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)
        pygame.display.set_caption("CityGraph + SUMO — Simulazione Interattiva")
        self._clock   = pygame.time.Clock()
        self._font_lg = pygame.font.SysFont("Consolas", 15, bold=True)
        self._font_md = pygame.font.SysFont("Consolas", 13)
        self._font_sm = pygame.font.SysFont("Consolas", 11)

        # ── Esporta rete SUMO ────────────────────────────────────────────────
        log.info("Conversione grafo → .net.xml (%d nodi, %d archi)...",
                 G.number_of_nodes(), G.number_of_edges())
        self.exporter = SumoNetExporter(G, osm_cache_path=osm_cache_path)
        self.net_path = os.path.join(data_dir, "city.net.xml")
        self.exporter.export(self.net_path)

        # ── Precomputa lista archi per il disegno ─────────────────────────────
        self._edge_draw_list = self._precompute_edge_draw_list()
        log.info("Edge disegnabili: %d / %d totali",
                 len(self._edge_draw_list), len(self.exporter.get_draw_edges()))

        # ── Camera (coordinate SUMO metriche) ────────────────────────────────
        bounds = self.exporter.get_bounds()
        if not bounds:
            raise ValueError("SumoNetExporter non ha prodotto geometria SUMO valida.")
        self._sumo_xmin, self._sumo_ymin, self._sumo_xmax, self._sumo_ymax = bounds
        log.info("Bbox SUMO: x=[%.0f, %.0f]  y=[%.0f, %.0f]",
                 self._sumo_xmin, self._sumo_xmax,
                 self._sumo_ymin, self._sumo_ymax)

        self._cam_zoom  = 1.0
        self._cam_off_x = 0.0
        self._cam_off_y = 0.0
        self._fit_camera()

        # ── SimulationController — import LAZY ───────────────────────────────
        self.scenario: Optional[ScenarioBuilder] = None
        self.sim      = None

        # ── Stato UI ─────────────────────────────────────────────────────────
        self._sim_running  = False
        self._paused       = False
        self._veh_type     = "car"
        self._spawn_count  = 10
        self._last_click_sumo: Optional[Tuple[float, float]] = None
        self._vehicles: List[Dict] = []
        self._stats:    Dict       = {}
        self._log_lines: List[str] = []
        self._bg: Optional[pygame.Surface] = None

        self._build_buttons()

    # ──────────────────────────────────────────
    # PRECOMPUTA LISTA ARCHI
    # ──────────────────────────────────────────

    def _precompute_edge_draw_list(self) -> List[Dict]:
        result  = []
        for edge in self.exporter.get_draw_edges():
            hw = edge.get("hw", "") or ""
            result.append({
                "hw":     hw,
                "shape":  edge["shape"],
                "color":  HW_COLOR.get(hw, C_ROAD),
                "width":  HW_WIDTH.get(hw, 1.5),
                "detail": hw in LOD_DETAIL_ONLY,
            })
        return result

    # ──────────────────────────────────────────
    # BOTTONI
    # ──────────────────────────────────────────

    def _build_buttons(self) -> None:
        px = MAP_W + 10
        bw, bh = PANEL_W - 20, 34

        self._btn_start = Button((px, 60,  bw, bh), "▶  Avvia Simulazione")
        self._btn_stop  = Button((px, 100, bw, bh), "■  Ferma Simulazione",
                                 color=C_BTN_STOP, active_color=C_BTN_STOP)
        self._btn_stop.enabled  = False
        self._btn_pause = Button((px, 140, bw, bh), "⏸  Pausa / Riprendi")
        self._btn_pause.enabled = False

        self._vtype_btns: Dict[str, Button] = {}
        for i, vt in enumerate(VEHICLE_TYPES.keys()):
            btn = Button((px, 210 + i * 38, bw, bh), vt.capitalize())
            self._vtype_btns[vt] = btn
        self._vtype_btns["car"].active = True

        last_y = 210 + len(VEHICLE_TYPES) * 38
        self._btn_spawn_random = Button(
            (px, last_y + 15, bw, bh), f"⚡ Spawn {self._spawn_count} random")
        self._btn_spawn_random.enabled = False

        hw2 = (bw - 10) // 2
        self._btn_spawn_minus = Button((px,          last_y + 55, hw2, 28), "−  meno")
        self._btn_spawn_plus  = Button((px + hw2+10, last_y + 55, hw2, 28), "+  più")
        self._btn_reset_cam   = Button((px, WINDOW_H - 80, bw, bh), "🔍 Reset Camera")

        self._all_buttons = [
            self._btn_start, self._btn_stop, self._btn_pause,
            *self._vtype_btns.values(),
            self._btn_spawn_random, self._btn_spawn_minus, self._btn_spawn_plus,
            self._btn_reset_cam,
        ]

    # ──────────────────────────────────────────
    # LOOP PRINCIPALE
    # ──────────────────────────────────────────

    def run(self) -> None:
        self._build_background()
        running = True

        while running:
            if self._sim_running and not self._paused and self.sim:
                self.sim.step()
                self._vehicles = self.sim.get_vehicle_positions()
                self._stats    = self.sim.get_stats()

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
                elif event.type == pygame.MOUSEWHEEL:
                    mx, my = pygame.mouse.get_pos()
                    if mx < MAP_W:
                        factor = ZOOM_STEP if event.y > 0 else 1.0 / ZOOM_STEP
                        self._zoom_at(mx, my, factor)
                        self._build_background()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        self._on_click(event.pos)

            keys  = pygame.key.get_pressed()
            moved = False
            spd   = PAN_SPEED / max(self._cam_zoom, 0.001)
            if keys[pygame.K_LEFT]  or keys[pygame.K_a]: self._cam_off_x += spd; moved = True
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]: self._cam_off_x -= spd; moved = True
            if keys[pygame.K_UP]    or keys[pygame.K_w]: self._cam_off_y += spd; moved = True
            if keys[pygame.K_DOWN]  or keys[pygame.K_s]: self._cam_off_y -= spd; moved = True
            if moved:
                self._build_background()

            self._draw()
            pygame.display.flip()
            self._clock.tick(60)

        self._cleanup()
        pygame.quit()

    # ──────────────────────────────────────────
    # CLICK
    # ──────────────────────────────────────────

    def _on_click(self, pos: Tuple[int, int]) -> None:
        mx, my = pos
        if mx >= MAP_W:
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
                self._btn_spawn_random.label = f"⚡ Spawn {self._spawn_count} random"
            elif self._btn_spawn_plus.hit(pos):
                self._spawn_count = min(200, self._spawn_count + 5)
                self._btn_spawn_random.label = f"⚡ Spawn {self._spawn_count} random"
            elif self._btn_reset_cam.hit(pos):
                self._fit_camera()
                self._build_background()
            else:
                for vt, btn in self._vtype_btns.items():
                    if btn.hit(pos):
                        self._veh_type = vt
                        for b in self._vtype_btns.values():
                            b.active = False
                        btn.active = True
                        break
            return

        sx, sy = self._screen_to_sumo(mx, my)
        self._last_click_sumo = (sx, sy)
        if self._sim_running and self.sim:
            vid = self.sim.spawn_at_xy(sx, sy, vtype=self._veh_type)
            self._log(f"Spawn {vid} ({self._veh_type})" if vid
                      else "Spawn fallito — clicca su una strada")
        else:
            self._log(f"Sim non attiva. Click a ({sx:.0f}, {sy:.0f})")

    # ──────────────────────────────────────────
    # SIMULAZIONE
    # ──────────────────────────────────────────

    def _start_simulation(self) -> None:
        if self._sim_running:
            return
        self._log("Avvio in corso...")
        try:
            # Import LAZY: libsumo/traci caricato solo qui
            from sumo.simulation_controller import SimulationController
            self.scenario = ScenarioBuilder(self.net_path)
            cfg = self.scenario.build()
            self.sim = SimulationController(cfg, self.exporter, self._veh_type)
            self.sim.start()
            self._sim_running = True
            self._paused      = False
            self._btn_start.enabled        = False
            self._btn_stop.enabled         = True
            self._btn_pause.enabled        = True
            self._btn_spawn_random.enabled = True
            self._log("Simulazione avviata!")
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
        self._paused      = False
        self._vehicles    = []
        self._stats       = {}
        self._btn_start.enabled        = True
        self._btn_stop.enabled         = False
        self._btn_pause.enabled        = False
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

    # ──────────────────────────────────────────
    # CAMERA
    # ──────────────────────────────────────────

    def _fit_camera(self) -> None:
        span_x = self._sumo_xmax - self._sumo_xmin
        span_y = self._sumo_ymax - self._sumo_ymin
        if span_x < 1 or span_y < 1:
            self._cam_zoom = 1.0
            self._cam_off_x = self._cam_off_y = 0.0
            return
        pad    = 0.06
        zoom_x = MAP_W    * (1 - 2 * pad) / span_x
        zoom_y = WINDOW_H * (1 - 2 * pad) / span_y
        self._cam_zoom = min(zoom_x, zoom_y)
        cx = (self._sumo_xmin + self._sumo_xmax) / 2
        cy = (self._sumo_ymin + self._sumo_ymax) / 2
        self._cam_off_x =  MAP_W    / 2 - cx  * self._cam_zoom
        self._cam_off_y =  WINDOW_H / 2 + cy  * self._cam_zoom

    def _zoom_at(self, px: int, py: int, factor: float) -> None:
        new_zoom = max(ZOOM_MIN, min(ZOOM_MAX, self._cam_zoom * factor))
        ratio = new_zoom / self._cam_zoom
        self._cam_off_x = px - ratio * (px - self._cam_off_x)
        self._cam_off_y = py - ratio * (py - self._cam_off_y)
        self._cam_zoom  = new_zoom

    def _sumo_to_screen(self, sx: float, sy: float) -> Tuple[int, int]:
        return (
            int( sx * self._cam_zoom + self._cam_off_x),
            int(-sy * self._cam_zoom + self._cam_off_y),
        )

    def _screen_to_sumo(self, px: int, py: int) -> Tuple[float, float]:
        return (
             (px - self._cam_off_x) / self._cam_zoom,
            -(py - self._cam_off_y) / self._cam_zoom,
        )

    def _on_map(self, px: int, py: int, margin: int = 0) -> bool:
        return -margin <= px <= MAP_W + margin and -margin <= py <= WINDOW_H + margin

    # ──────────────────────────────────────────
    # BACKGROUND (cache, ricostruita ad ogni pan/zoom)
    # ──────────────────────────────────────────

    def _build_background(self) -> None:
        surf = pygame.Surface((MAP_W, WINDOW_H))
        surf.fill(C_BG)

        zoom        = self._cam_zoom
        show_detail = zoom >= LOD_DETAIL_ZOOM_MIN

        for e in self._edge_draw_list:
            if e["detail"] and not show_detail:
                continue
            pts = [self._sumo_to_screen(x, y) for x, y in e["shape"]]
            if len(pts) < 2:
                continue
            width = max(1, min(12, int(e["width"] * zoom)))
            if not any(self._on_map(px, py, 30) for px, py in pts):
                continue
            pygame.draw.lines(surf, e["color"], False, pts, width)

        self._bg = surf

    # ──────────────────────────────────────────
    # DRAW
    # ──────────────────────────────────────────

    def _draw(self) -> None:
        screen = self._screen
        if self._bg:
            screen.blit(self._bg, (0, 0))
        else:
            pygame.draw.rect(screen, C_BG, (0, 0, MAP_W, WINDOW_H))

        if self._last_click_sumo:
            cx, cy = self._sumo_to_screen(*self._last_click_sumo)
            if self._on_map(cx, cy, 5):
                pygame.draw.circle(screen, C_SPAWN_MK, (cx, cy), 8, 2)
                pygame.draw.line(screen, C_SPAWN_MK, (cx-12, cy), (cx+12, cy), 1)
                pygame.draw.line(screen, C_SPAWN_MK, (cx, cy-12), (cx, cy+12), 1)

        veh_r = max(3, int(5 * self._cam_zoom * 0.25))
        for v in self._vehicles:
            px, py = self._sumo_to_screen(v["x"], v["y"])
            if not self._on_map(px, py):
                continue
            col = C_VEH.get(v.get("type", ""), C_VEH_DEF)
            pygame.draw.circle(screen, col, (px, py), veh_r)
            if self._cam_zoom > 4:
                pygame.draw.circle(screen, C_WHITE, (px, py), veh_r, 1)

        self._draw_panel(screen)
        self._draw_map_hud(screen)

    def _draw_panel(self, screen: pygame.Surface) -> None:
        pygame.draw.rect(screen, C_PANEL, (MAP_W, 0, PANEL_W, WINDOW_H))
        pygame.draw.line(screen, C_PANEL_BD, (MAP_W, 0), (MAP_W, WINDOW_H), 2)

        px0  = MAP_W + 10
        f_lg, f_md, f_sm = self._font_lg, self._font_md, self._font_sm

        self._text(screen, "SUMO + Neo4j",           (px0, 10), f_lg, C_WHITE)
        self._text(screen, "Simulazione Interattiva", (px0, 28), f_sm, C_TEXT_DIM)

        for btn in self._all_buttons:
            btn.draw(screen, f_md)

        self._text(screen, "Tipo veicolo:", (px0, 190), f_sm, C_TEXT_DIM)

        stats_y = 210 + len(VEHICLE_TYPES) * 38 + 100
        self._text(screen, "─── Statistiche ───", (px0, stats_y), f_sm, C_TEXT_DIM)
        stats_y += 18

        if self._sim_running and self._stats:
            lines = [
                f"Tempo sim : {self._stats.get('time', 0):.1f} s",
                f"Veicoli   : {self._stats.get('vehicles_active', 0)}",
                f"Totale sp.: {self._stats.get('vehicles_total', 0)}",
                f"Teleport  : {self._stats.get('teleports', 0)}",
                f"Collisioni: {self._stats.get('collisions', 0)}",
            ]
        else:
            status = "PAUSA" if self._paused else ("IN CORSO" if self._sim_running else "FERMA")
            lines = [
                f"Stato     : {status}",
                f"Nodi      : {self.G.number_of_nodes()}",
                f"Archi     : {self.G.number_of_edges()}",
                f"Zoom      : {self._cam_zoom:.3f}",
            ]
        for line in lines:
            self._text(screen, line, (px0, stats_y), f_sm, C_TEXT)
            stats_y += 16

        log_y = WINDOW_H - 200
        self._text(screen, "─── Log ───", (px0, log_y), f_sm, C_TEXT_DIM)
        log_y += 16
        for line in self._log_lines[-8:]:
            self._text(screen, line[:34], (px0, log_y), f_sm, C_TEXT_DIM)
            log_y += 14

        fps = self._clock.get_fps()
        self._text(screen, f"FPS:{fps:.0f}  Zoom:{self._cam_zoom:.3f}",
                   (px0, WINDOW_H - 20), f_sm, C_TEXT_DIM)

    def _draw_map_hud(self, screen: pygame.Surface) -> None:
        f = self._font_sm
        lines = [
            "WASD/Frecce=Pan  Scroll=Zoom  R=Reset  ESC=Esci",
            "Sim attiva: click sulla mappa per spawnare veicoli",
        ]
        if self._paused:
            lines.insert(0, "⏸ PAUSA — [Spazio] per riprendere")
        y = 6
        for line in lines:
            ts = f.render(line, True, C_TEXT)
            bg = pygame.Surface((ts.get_width()+8, ts.get_height()+4), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 140))
            screen.blit(bg, (4, y-2))
            screen.blit(ts, (8, y))
            y += ts.get_height() + 3

    # ──────────────────────────────────────────
    # UTILITY
    # ──────────────────────────────────────────

    @staticmethod
    def _text(surf: pygame.Surface, text: str, pos: Tuple,
              font: pygame.font.Font, color: Tuple) -> None:
        surf.blit(font.render(text, True, color), pos)

    def _log(self, msg: str) -> None:
        log.info("[SumoRenderer] %s", msg)
        self._log_lines.append(msg)
        if len(self._log_lines) > 50:
            self._log_lines = self._log_lines[-50:]

    def _cleanup(self) -> None:
        if self._sim_running:
            self._stop_simulation()
