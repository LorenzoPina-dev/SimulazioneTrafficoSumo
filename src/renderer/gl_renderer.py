"""
gl_renderer.py — Renderer GPU-accelerato con ModernGL + Pygame.

Strategia di rendering:
  - Tutti gli archi vengono caricati una volta sola in un VBO (Vertex Buffer Object)
  - Ogni frame: 1 draw call per le strade (GL_LINES), 1 per il percorso
  - I nodi vengono disegnati con GL_POINTS con point-size variabile
  - Il pan/zoom si applica come uniform alla view-projection matrix → zero rebind
  - La cache del VBO si invalida solo quando cambia lo zoom tier (LOD)
"""

import logging
import struct
from typing import Optional, List, Dict, Tuple

import numpy as np
import moderngl
import pygame
import networkx as nx

from config.settings import RENDERER, CAMERA as CAM_CFG
from renderer.camera import Camera

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Colori per tipo di highway (RGBA float32)
# ─────────────────────────────────────────────
_HW_COLOR: Dict[str, Tuple[float, float, float, float]] = {
    "motorway":      (1.00, 0.63, 0.20, 1.0),
    "trunk":         (1.00, 0.71, 0.31, 1.0),
    "primary":       (1.00, 0.86, 0.39, 1.0),
    "secondary":     (0.78, 0.78, 0.47, 1.0),
    "tertiary":      (0.63, 0.67, 0.59, 1.0),
    "residential":   (0.47, 0.51, 0.59, 1.0),
    "service":       (0.35, 0.39, 0.47, 1.0),
    "footway":       (0.55, 0.75, 0.55, 1.0),
    "cycleway":      (0.39, 0.78, 0.55, 1.0),
    "path":          (0.47, 0.67, 0.47, 0.7),
}
_DEFAULT_COLOR = RENDERER.road_color + (1.0,) if len(RENDERER.road_color) == 3 else RENDERER.road_color

# ─────────────────────────────────────────────
# GLSL Shaders
# ─────────────────────────────────────────────
_VERT_SHADER = """
#version 330 core
in vec2 in_position;
in vec4 in_color;
out vec4 v_color;
uniform mat3 u_transform;
void main() {
    vec3 p = u_transform * vec3(in_position, 1.0);
    // NDC: x già in [-1,1], y già in [-1,1]
    gl_Position = vec4(p.xy, 0.0, 1.0);
    gl_PointSize = 6.0;
    v_color = in_color;
}
"""

_FRAG_SHADER = """
#version 330 core
in vec4 v_color;
out vec4 f_color;
void main() {
    f_color = v_color;
}
"""

_FRAG_ROUND_POINT = """
#version 330 core
in vec4 v_color;
out vec4 f_color;
void main() {
    // Scarta i frammenti fuori dal cerchio (per GL_POINTS)
    vec2 coord = gl_PointCoord * 2.0 - 1.0;
    if (dot(coord, coord) > 1.0) discard;
    f_color = v_color;
}
"""


class GLRenderer:
    """
    Renderer OpenGL per la mappa stradale.

    Ciclo di vita:
      1. __init__() — prepara strutture dati
      2. run()      — apre la finestra e fa il loop principale
    """

    def __init__(self, G: nx.DiGraph, pois: List[Dict] = None):
        self.G    = G
        self.pois = pois or []

        self.camera = Camera(
            width=RENDERER.window_width,
            height=RENDERER.window_height,
        )

        # Stato UI
        self._path_nodes: List[int]    = []
        self._route_start: Optional[int] = None
        self._running = False

        # ModernGL handles
        self._ctx: Optional[moderngl.Context]  = None
        self._prog: Optional[moderngl.Program] = None
        self._prog_pt: Optional[moderngl.Program] = None

        # VBO / VAO per le strade (rebuild solo a LOD change)
        self._road_vao: Optional[moderngl.VertexArray] = None
        self._road_vbo: Optional[moderngl.Buffer]      = None
        self._road_vertex_count: int = 0

        # VBO / VAO per i nodi
        self._node_vao: Optional[moderngl.VertexArray] = None
        self._node_vbo: Optional[moderngl.Buffer]      = None
        self._node_vertex_count: int = 0

        # VBO per il percorso (rebuild ad ogni cambio path)
        self._path_vao: Optional[moderngl.VertexArray] = None
        self._path_vbo: Optional[moderngl.Buffer]      = None
        self._path_vertex_count: int = 0

        # VBO per i POI
        self._poi_vao: Optional[moderngl.VertexArray] = None
        self._poi_vbo: Optional[moderngl.Buffer]      = None
        self._poi_vertex_count: int = 0

        # Coordinate normalizzate (lon,lat → NDC world space)
        lats = [d["lat"] for _, d in G.nodes(data=True) if "lat" in d]
        lons = [d["lon"] for _, d in G.nodes(data=True) if "lon" in d]
        if lats and lons:
            self.camera.fit_to_bbox(min(lons), min(lats), max(lons), max(lats))

        # Fonts Pygame per HUD
        self._font = None
        self._clock = None
        self._prev_zoom_tier = -1
        self._path_dirty = False

    # ──────────────────────────────────────────
    # LOOP PRINCIPALE
    # ──────────────────────────────────────────

    def run(self) -> None:
        pygame.init()

        # Finestra con flag OpenGL
        pygame.display.set_caption(RENDERER.title)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK,
                                        pygame.GL_CONTEXT_PROFILE_CORE)
        if RENDERER.msaa_samples > 0:
            pygame.display.gl_set_attribute(pygame.GL_MULTISAMPLEBUFFERS, 1)
            pygame.display.gl_set_attribute(pygame.GL_MULTISAMPLESAMPLES,
                                            RENDERER.msaa_samples)

        self._screen = pygame.display.set_mode(
            (RENDERER.window_width, RENDERER.window_height),
            pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE,
        )
        self._clock = pygame.time.Clock()
        self._font  = pygame.font.SysFont("Consolas", 14)

        # Crea contesto ModernGL dallo stato OpenGL corrente di Pygame
        self._ctx = moderngl.create_context()
        if RENDERER.msaa_samples > 0:
            self._ctx.multisample = True
        self._ctx.enable(moderngl.PROGRAM_POINT_SIZE)
        self._ctx.enable(moderngl.BLEND)
        self._ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

        # Compila shaders
        self._prog    = self._ctx.program(vertex_shader=_VERT_SHADER, fragment_shader=_FRAG_SHADER)
        self._prog_pt = self._ctx.program(vertex_shader=_VERT_SHADER, fragment_shader=_FRAG_ROUND_POINT)

        # Carica le geometrie statiche una sola volta
        self._build_road_buffers()
        self._build_node_buffers()
        self._build_poi_buffers()

        self._running = True
        log.info("GLRenderer avviato | nodi=%d archi=%d",
                 self.G.number_of_nodes(), self.G.number_of_edges())

        while self._running:
            self._handle_events()
            if self._path_dirty:
                self._build_path_buffers()
                self._path_dirty = False
            self._draw()
            self._draw_hud()
            pygame.display.flip()
            self._clock.tick(RENDERER.target_fps)

        self._cleanup()
        pygame.quit()

    # ──────────────────────────────────────────
    # BUILD BUFFERS (una sola volta / on-demand)
    # ──────────────────────────────────────────

    def _build_road_buffers(self) -> None:
        """
        Crea il VBO con tutti gli archi del grafo.
        Formato vertice: [x_ndc, y_ndc, r, g, b, a]  → 6 × float32 = 24 byte
        Ogni arco = 2 vertici → GL_LINES
        """
        log.info("Building road VBO...")
        verts = []
        for u, v, data in self.G.edges(data=True):
            nu = self.G.nodes.get(u, {})
            nv = self.G.nodes.get(v, {})
            if "lat" not in nu or "lat" not in nv:
                continue
            hw = data.get("highway", "")
            r, g, b, a = _HW_COLOR.get(hw, _DEFAULT_COLOR)
            for node in (nu, nv):
                wx, wy = self.camera.world_to_ndc(node["lon"], node["lat"])
                verts.extend([wx, wy, r, g, b, a])

        if not verts:
            log.warning("Nessun arco nel VBO.")
            return

        arr = np.array(verts, dtype=np.float32)
        self._road_vbo = self._ctx.buffer(arr.tobytes())
        self._road_vao = self._ctx.vertex_array(
            self._prog,
            [(self._road_vbo, "2f 4f", "in_position", "in_color")],
        )
        self._road_vertex_count = len(verts) // 6
        log.info("Road VBO: %d vertici (%.1f MB)", self._road_vertex_count,
                 arr.nbytes / 1e6)

    def _build_node_buffers(self) -> None:
        """VBO per i nodi (GL_POINTS)."""
        r, g, b, a = RENDERER.node_color
        verts = []
        for nid, data in self.G.nodes(data=True):
            if "lat" not in data:
                continue
            wx, wy = self.camera.world_to_ndc(data["lon"], data["lat"])
            verts.extend([wx, wy, r, g, b, a])
        if not verts:
            return
        arr = np.array(verts, dtype=np.float32)
        self._node_vbo = self._ctx.buffer(arr.tobytes())
        self._node_vao = self._ctx.vertex_array(
            self._prog_pt,
            [(self._node_vbo, "2f 4f", "in_position", "in_color")],
        )
        self._node_vertex_count = len(verts) // 6

    def _build_path_buffers(self) -> None:
        """VBO per il percorso evidenziato — rebuild ad ogni nuovo path."""
        if self._path_vao:
            self._path_vao.release()
            self._path_vbo.release()
            self._path_vao = self._path_vbo = None
        if len(self._path_nodes) < 2:
            self._path_vertex_count = 0
            return
        r, g, b, a = RENDERER.road_highlight
        verts = []
        for i in range(len(self._path_nodes) - 1):
            for nid in (self._path_nodes[i], self._path_nodes[i + 1]):
                data = self.G.nodes.get(nid, {})
                if "lat" not in data:
                    continue
                wx, wy = self.camera.world_to_ndc(data["lon"], data["lat"])
                verts.extend([wx, wy, r, g, b, 1.0])
        if not verts:
            return
        arr = np.array(verts, dtype=np.float32)
        self._path_vbo = self._ctx.buffer(arr.tobytes())
        self._path_vao = self._ctx.vertex_array(
            self._prog,
            [(self._path_vbo, "2f 4f", "in_position", "in_color")],
        )
        self._path_vertex_count = len(verts) // 6

    def _build_poi_buffers(self) -> None:
        if not self.pois:
            return
        r, g, b, a = RENDERER.poi_color
        verts = []
        for poi in self.pois:
            try:
                lat, lon = float(poi["lat"]), float(poi["lon"])
            except (KeyError, ValueError):
                continue
            wx, wy = self.camera.world_to_ndc(lon, lat)
            verts.extend([wx, wy, r, g, b, a])
        if not verts:
            return
        arr = np.array(verts, dtype=np.float32)
        self._poi_vbo = self._ctx.buffer(arr.tobytes())
        self._poi_vao = self._ctx.vertex_array(
            self._prog_pt,
            [(self._poi_vbo, "2f 4f", "in_position", "in_color")],
        )
        self._poi_vertex_count = len(verts) // 6

    # ──────────────────────────────────────────
    # TRANSFORM MATRIX (pan + zoom → NDC)
    # ──────────────────────────────────────────

    def _get_transform(self) -> Tuple:
        """
        Restituisce la matrice 3×3 (column-major) che trasforma world NDC
        nei device NDC tenendo conto di pan e zoom.
        """
        cam = self.camera
        # Scala: zoom uniforme
        sx = cam.zoom
        sy = cam.zoom
        # Traslazione in NDC (offset_x/y sono in pixel, convertiamo)
        tx = cam.offset_x / (cam.width  * 0.5)
        ty = -cam.offset_y / (cam.height * 0.5)
        # Matrice 3x3 column-major flat
        # [sx, 0, tx,
        #  0, sy, ty,
        #  0,  0,  1]
        return (
            sx,  0.0, 0.0,
            0.0, sy,  0.0,
            tx,  ty,  1.0,
        )

    # ──────────────────────────────────────────
    # DRAW
    # ──────────────────────────────────────────

    def _draw(self) -> None:
        bg = RENDERER.bg_color
        self._ctx.clear(*bg)

        transform = self._get_transform()

        # ── Strade ────────────────────────────
        if self._road_vao and self._road_vertex_count > 0:
            self._prog["u_transform"].write(
                np.array(transform, dtype=np.float32).tobytes()
            )
            lw = max(1.0, min(
                RENDERER.road_width_base * self.camera.zoom,
                RENDERER.max_road_width,
            ))
            self._ctx.line_width = lw
            self._road_vao.render(moderngl.LINES, vertices=self._road_vertex_count)

        # ── Percorso ──────────────────────────
        if self._path_vao and self._path_vertex_count > 0:
            self._prog["u_transform"].write(
                np.array(transform, dtype=np.float32).tobytes()
            )
            self._ctx.line_width = max(3.0, RENDERER.route_width * self.camera.zoom * 0.4)
            self._path_vao.render(moderngl.LINES, vertices=self._path_vertex_count)

        # ── POI ───────────────────────────────
        if (RENDERER.draw_pois and self._poi_vao and
                self._poi_vertex_count > 0 and
                self.camera.zoom >= RENDERER.poi_zoom_min):
            self._prog_pt["u_transform"].write(
                np.array(transform, dtype=np.float32).tobytes()
            )
            self._ctx.point_size = max(4.0, 8.0 * self.camera.zoom * 0.3)
            self._poi_vao.render(moderngl.POINTS, vertices=self._poi_vertex_count)

        # ── Nodi ──────────────────────────────
        if (RENDERER.draw_nodes and self._node_vao and
                self._node_vertex_count > 0 and
                self.camera.zoom >= RENDERER.node_zoom_min):
            self._prog_pt["u_transform"].write(
                np.array(transform, dtype=np.float32).tobytes()
            )
            self._ctx.point_size = max(2.0, RENDERER.node_radius * self.camera.zoom * 0.5)
            self._node_vao.render(moderngl.POINTS, vertices=self._node_vertex_count)

    def _draw_hud(self) -> None:
        """
        HUD testuale disegnato con Pygame sopra il layer OpenGL.
        Usa pygame.surfarray per blit diretto nella framebuffer corrente.
        """
        fps  = self._clock.get_fps()
        cam  = self.camera
        lines = [
            f"FPS:{fps:.0f}  Nodi:{self.G.number_of_nodes()}  "
            f"Archi:{self.G.number_of_edges()}  Zoom:{cam.zoom:.2f}",
            "WASD/Frecce=Pan  Scroll=Zoom  Click=Nodo  R=Reset  C=Clear  ESC=Esci",
        ]
        if self._path_nodes:
            lines.append(f"Percorso: {len(self._path_nodes)} nodi")
        if self._route_start:
            lines.append(f"Partenza selezionata: {self._route_start}  (clicca destinazione)")

        # Disegna il testo su una surface pygame e lo blit come texture
        # Usiamo il metodo classico: salviamo lo stato OpenGL, disegnamo in 2D
        w, h = pygame.display.get_surface().get_size()
        hud_surf = pygame.Surface((w, 80), pygame.SRCALPHA)
        hud_surf.fill((0, 0, 0, 0))
        y = 4
        for line in lines:
            ts = self._font.render(line, True, (220, 220, 240))
            bg = pygame.Surface((ts.get_width() + 8, ts.get_height() + 4), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 150))
            hud_surf.blit(bg, (4, y - 2))
            hud_surf.blit(ts, (8, y))
            y += ts.get_height() + 3

        # Converti surface in texture OpenGL e blit come quad 2D fullscreen
        raw = pygame.image.tostring(hud_surf, "RGBA", True)
        tex = self._ctx.texture((w, 80), 4, raw)
        tex.filter = moderngl.LINEAR, moderngl.LINEAR

        # Quad NDC che copre la parte superiore dello schermo
        quad_verts = np.array([
            -1.0,  1.0, 0.0, 0.0,
             1.0,  1.0, 1.0, 0.0,
            -1.0,  1.0 - (160.0 / h) * 2, 0.0, 1.0,
             1.0,  1.0 - (160.0 / h) * 2, 1.0, 1.0,
        ], dtype=np.float32)

        if not hasattr(self, "_hud_prog"):
            self._hud_prog = self._ctx.program(
                vertex_shader="""
                    #version 330 core
                    in vec2 in_pos; in vec2 in_uv;
                    out vec2 v_uv;
                    void main() { gl_Position = vec4(in_pos, 0.0, 1.0); v_uv = in_uv; }
                """,
                fragment_shader="""
                    #version 330 core
                    in vec2 v_uv; out vec4 f_color;
                    uniform sampler2D tex;
                    void main() { f_color = texture(tex, v_uv); }
                """,
            )
        hud_vbo = self._ctx.buffer(quad_verts.tobytes())
        hud_vao = self._ctx.vertex_array(
            self._hud_prog,
            [(hud_vbo, "2f 2f", "in_pos", "in_uv")],
        )
        tex.use(0)
        self._hud_prog["tex"] = 0
        hud_vao.render(moderngl.TRIANGLE_STRIP)
        tex.release()
        hud_vbo.release()
        hud_vao.release()

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
                    self._route_start = None
                    self._path_dirty  = True

            elif event.type == pygame.MOUSEWHEEL:
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
        node = self._find_nearest_ndc_node(pos)
        if node is None:
            return
        if self._route_start is None:
            self._route_start = node
            self._path_nodes  = []
            self._path_dirty  = True
        else:
            from graph.algorithms import GraphAlgorithms
            path = GraphAlgorithms.shortest_path(self.G, self._route_start, node)
            if path:
                self._path_nodes = path
                log.info("Percorso: %d nodi", len(path))
            else:
                log.warning("Nessun percorso trovato")
            self._route_start = None
            self._path_dirty  = True

    def _find_nearest_ndc_node(self, pos: Tuple[int, int], threshold_px: int = 25) -> Optional[int]:
        """Trova il nodo più vicino al click in coordinate pixel."""
        px, py = pos
        best, best_d = None, threshold_px ** 2
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
    # HELPERS
    # ──────────────────────────────────────────

    def _reset_view(self) -> None:
        lats = [d["lat"] for _, d in self.G.nodes(data=True) if "lat" in d]
        lons = [d["lon"] for _, d in self.G.nodes(data=True) if "lon" in d]
        if lats and lons:
            self.camera.fit_to_bbox(min(lons), min(lats), max(lons), max(lats))

    def _cleanup(self) -> None:
        for attr in ("_road_vao", "_road_vbo", "_node_vao", "_node_vbo",
                     "_path_vao", "_path_vbo", "_poi_vao", "_poi_vbo"):
            obj = getattr(self, attr, None)
            if obj:
                obj.release()
        if self._prog:
            self._prog.release()
        if self._prog_pt:
            self._prog_pt.release()
        if self._ctx:
            self._ctx.release()

    def set_path(self, node_ids: List[int]) -> None:
        self._path_nodes = node_ids
        self._path_dirty = True
