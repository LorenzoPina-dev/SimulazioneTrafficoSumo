"""
map_renderer.py — Rendering della mappa con Pygame (fallback senza OpenGL).
Gestisce: strade con LOD progressivo, nodi, POI, percorso evidenziato, HUD info.
"""

import logging
from typing import Optional, List, Dict, Tuple

import pygame
import networkx as nx

from config.settings import RENDERER, CAMERA as CAM_CFG, LOD_LEVELS
from renderer.camera import Camera

log = logging.getLogger(__name__)

# Spessore strade per tipo (pixel a zoom=1)
HIGHWAY_WIDTH = {
    "motorway": 4.0, "trunk": 3.5, "primary": 3.0, "secondary": 2.5,
    "tertiary": 2.0, "unclassified": 1.5, "residential": 1.5,
    "service": 1.0, "living_street": 1.2, "pedestrian": 1.0,
    "footway": 0.8, "cycleway": 0.8, "path": 0.7,
    "motorway_link": 2.0, "trunk_link": 2.0, "primary_link": 2.0,
    "secondary_link": 1.5, "tertiary_link": 1.5,
}

HIGHWAY_COLOR = {
    "motorway":  (255, 160,  50),
    "trunk":     (255, 180,  80),
    "primary":   (255, 220, 100),
    "secondary": (200, 200, 120),
    "tertiary":  (160, 170, 150),
}


class MapRenderer:
    """
    Renderer Pygame del grafo città (fallback senza ModernGL).
    Supporta LOD progressivo e zoom centrato sul mouse.
    """

    def __init__(self, G: nx.DiGraph, pois: List[Dict] = None):
        self.G    = G
        self.pois = pois or []
        self.camera = Camera(
            width=RENDERER.window_width,
            height=RENDERER.window_height,
        )
        self._path_nodes: List[int] = []
        self._selected_node: Optional[int] = None
        self._route_start: Optional[int] = None
        self._font: Optional[pygame.font.Font] = None
        self._font_sm: Optional[pygame.font.Font] = None
        self._running = False
        self._screen: Optional[pygame.Surface] = None
        self._clock: Optional[pygame.time.Clock] = None
        self._hud_lines: List[str] = []

        lats = [d["lat"] for _, d in G.nodes(data=True) if "lat" in d]
        lons = [d["lon"] for _, d in G.nodes(data=True) if "lon" in d]
        if lats and lons:
            self.camera.fit_to_bbox(min(lons), min(lats), max(lons), max(lats))

    # ──────────────────────────────────────────
    # LOOP PRINCIPALE
    # ──────────────────────────────────────────

    def run(self) -> None:
        pygame.init()
        pygame.display.set_caption(RENDERER.title)
        self._screen = pygame.display.set_mode(
            (RENDERER.window_width, RENDERER.window_height),
            pygame.RESIZABLE,
        )
        self._clock   = pygame.time.Clock()
        self._font    = pygame.font.SysFont("Consolas", 14)
        self._font_sm = pygame.font.SysFont("Consolas", 11)
        self._running = True
        log.info("MapRenderer (Pygame) avviato.")

        while self._running:
            self._handle_events()
            self._update()
            self._draw()
            self._clock.tick(RENDERER.target_fps)

        pygame.quit()

    # ──────────────────────────────────────────
    # EVENTI
    # ──────────────────────────────────────────

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self._running = False
                elif event.key == pygame.K_r:
                    self._reset_view()
                elif event.key == pygame.K_c:
                    self._path_nodes.clear()
                    self._selected_node = None
                    self._route_start = None

            elif event.type == pygame.MOUSEWHEEL:
                # Zoom centrato sulla posizione del mouse
                mx, my = pygame.mouse.get_pos()
                factor = CAM_CFG.zoom_step if event.y > 0 else 1.0 / CAM_CFG.zoom_step
                self.camera.zoom_at(mx, my, factor)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    self._on_left_click(event.pos)

            elif event.type == pygame.VIDEORESIZE:
                self.camera.width  = event.w
                self.camera.height = event.h

        keys = pygame.key.get_pressed()
        spd  = CAM_CFG.pan_speed
        if keys[pygame.K_LEFT]  or keys[pygame.K_a]: self.camera.pan( spd, 0)
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]: self.camera.pan(-spd, 0)
        if keys[pygame.K_UP]    or keys[pygame.K_w]: self.camera.pan(0,  spd)
        if keys[pygame.K_DOWN]  or keys[pygame.K_s]: self.camera.pan(0, -spd)

    def _on_left_click(self, pos: Tuple[int, int]) -> None:
        node = self._find_nearest_screen_node(pos)
        if node is None:
            return
        if self._route_start is None:
            self._route_start   = node
            self._selected_node = node
            self._path_nodes    = []
        else:
            from graph.algorithms import GraphAlgorithms
            path = GraphAlgorithms.shortest_path(self.G, self._route_start, node)
            if path:
                self._path_nodes = path
                log.info("Percorso trovato: %d nodi", len(path))
            else:
                log.warning("Nessun percorso trovato")
            self._route_start   = None
            self._selected_node = node

    def _find_nearest_screen_node(self, pos: Tuple[int, int], threshold: int = 30) -> Optional[int]:
        px, py = pos
        best, best_d = None, threshold ** 2
        for nid, data in self.G.nodes(data=True):
            if "lat" not in data:
                continue
            sx, sy = self.camera.world_to_screen(data["lon"], data["lat"])
            d = (sx - px) ** 2 + (sy - py) ** 2
            if d < best_d:
                best_d = d
                best   = nid
        return best

    # ──────────────────────────────────────────
    # UPDATE
    # ──────────────────────────────────────────

    def _update(self) -> None:
        fps  = self._clock.get_fps()
        zoom = self.camera.zoom

        # Trova label LOD attuale
        lod_label = LOD_LEVELS[0].label
        for lod in LOD_LEVELS:
            if zoom >= lod.zoom_min:
                lod_label = lod.label

        meta = f"Nodi:{self.G.number_of_nodes()} Archi:{self.G.number_of_edges()}"
        path_info = f"Percorso:{len(self._path_nodes)} nodi" if self._path_nodes else ""
        sel_info  = f"Sel:{self._route_start}" if self._route_start else ""
        self._hud_lines = [
            f"FPS:{fps:.0f}  {meta}  Zoom:{zoom:.2f}  LOD:{lod_label}",
            "WASD/Frecce=Pan  Scroll=Zoom (centrato sul mouse)  R=Reset  C=Clear  ESC=Esci",
            " | ".join(filter(None, [path_info, sel_info])),
        ]

    # ──────────────────────────────────────────
    # DRAW
    # ──────────────────────────────────────────

    def _draw(self) -> None:
        self._screen.fill(RENDERER.bg_color)
        self._draw_grid()
        self._draw_edges()
        self._draw_path()
        if RENDERER.draw_pois:
            self._draw_pois()
        if RENDERER.draw_nodes:
            self._draw_nodes()
        self._draw_hud()
        pygame.display.flip()

    def _draw_grid(self) -> None:
        surf = self._screen
        W, H = RENDERER.window_width, RENDERER.window_height
        step = 80
        for x in range(0, W, step):
            pygame.draw.line(surf, RENDERER.grid_color, (x, 0), (x, H))
        for y in range(0, H, step):
            pygame.draw.line(surf, RENDERER.grid_color, (0, y), (W, y))

    def _draw_edges(self) -> None:
        """Disegna solo le strade visibili al LOD corrente."""
        cam  = self.camera
        surf = self._screen
        zoom = cam.zoom
        path_set = set(zip(self._path_nodes, self._path_nodes[1:])) if self._path_nodes else set()

        # Determina quali tipi di highway sono visibili al zoom corrente
        visible_types: set = set()
        for lod in LOD_LEVELS:
            if zoom >= lod.zoom_min:
                visible_types.update(lod.highway_types)

        for u, v, data in self.G.edges(data=True):
            hw = data.get("highway", "")
            if hw not in visible_types:
                continue
            if (u, v) in path_set:
                continue
            nu = self.G.nodes.get(u, {})
            nv = self.G.nodes.get(v, {})
            if "lat" not in nu or "lat" not in nv:
                continue
            p1 = cam.world_to_screen(nu["lon"], nu["lat"])
            p2 = cam.world_to_screen(nv["lon"], nv["lat"])
            if not cam.is_on_screen(*p1) and not cam.is_on_screen(*p2):
                continue
            color = HIGHWAY_COLOR.get(hw, RENDERER.road_color)
            width = max(1, int(HIGHWAY_WIDTH.get(hw, RENDERER.road_width_base) * zoom * 0.6))
            pygame.draw.line(surf, color, p1, p2, min(width, 8))

    def _draw_path(self) -> None:
        if len(self._path_nodes) < 2:
            return
        cam  = self.camera
        surf = self._screen
        pts  = []
        for nid in self._path_nodes:
            data = self.G.nodes.get(nid, {})
            if "lat" in data:
                pts.append(cam.world_to_screen(data["lon"], data["lat"]))
        if len(pts) >= 2:
            pygame.draw.lines(surf, RENDERER.road_highlight, False, pts,
                              max(3, int(4 * cam.zoom * 0.5)))

    def _draw_nodes(self) -> None:
        """
        Disegna i nodi (punti blu = intersezioni stradali).
        Visibili solo quando zoom >= RENDERER.node_zoom_min.
        """
        if self.camera.zoom < RENDERER.node_zoom_min:
            return
        cam  = self.camera
        surf = self._screen
        r    = RENDERER.node_radius
        path_set = set(self._path_nodes)
        for nid, data in self.G.nodes(data=True):
            if "lat" not in data:
                continue
            px, py = cam.world_to_screen(data["lon"], data["lat"])
            if not cam.is_on_screen(px, py):
                continue
            color = RENDERER.node_highlight if nid in path_set else RENDERER.node_color
            if nid == self._route_start:
                color = (0, 255, 100)
            pygame.draw.circle(surf, color, (px, py), r)

    def _draw_pois(self) -> None:
        cam  = self.camera
        surf = self._screen
        for poi in self.pois:
            px, py = cam.world_to_screen(float(poi["lon"]), float(poi["lat"]))
            if not cam.is_on_screen(px, py):
                continue
            pygame.draw.circle(surf, RENDERER.poi_color, (px, py), 5)
            if cam.zoom > 3:
                label = self._font_sm.render(poi.get("name", "")[:20], True, RENDERER.poi_color)
                surf.blit(label, (px + 6, py - 6))

    def _draw_hud(self) -> None:
        surf = self._screen
        y = 8
        for line in self._hud_lines:
            if not line.strip():
                continue
            surf_txt = self._font.render(line, True, RENDERER.text_color)
            bg = pygame.Surface((surf_txt.get_width() + 8, surf_txt.get_height() + 4), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 140))
            surf.blit(bg, (4, y - 2))
            surf.blit(surf_txt, (8, y))
            y += surf_txt.get_height() + 4

    # ──────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────

    def _reset_view(self) -> None:
        lats = [d["lat"] for _, d in self.G.nodes(data=True) if "lat" in d]
        lons = [d["lon"] for _, d in self.G.nodes(data=True) if "lon" in d]
        if lats and lons:
            self.camera.fit_to_bbox(min(lons), min(lats), max(lons), max(lats))

    def set_path(self, node_ids: List[int]) -> None:
        self._path_nodes = node_ids
