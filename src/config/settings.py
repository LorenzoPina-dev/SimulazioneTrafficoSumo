"""
settings.py — Configurazione centralizzata e parametrica dell'applicazione.

Priorità (dalla più alta):
  1. Variabili d'ambiente (Docker / shell)  ← usate nel container
  2. Valori di default qui sotto            ← usate sull'host

Così la stessa codebase gira sia in Docker sia in locale senza modifiche.
"""

from dataclasses import dataclass, field
from typing import Optional
import os

# ─────────────────────────────────────────────
# Helper: legge env oppure usa il default
# ─────────────────────────────────────────────
def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default

def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, str(default)).lower()
    return v in ("1", "true", "yes")

# ─────────────────────────────────────────────
# DIRECTORY BASE
# ─────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR  = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")

# ─────────────────────────────────────────────
# CITTÀ / QUERY OSM DEFAULT
# ─────────────────────────────────────────────
@dataclass
class CityConfig:
    name: str           = ""
    country: str        = "Italy"
    osm_area_query: str = ""
    bbox: Optional[tuple] = None  # (lat_min, lat_max, lon_min, lon_max) oppure None

def _default_city() -> "CityConfig":
    raw   = _env("APP_CITY", "Como, Italy")
    name  = raw.split(",")[0].strip()
    return CityConfig(name=name, country="Italy", osm_area_query=raw)

DEFAULT_CITY = _default_city()

# ─────────────────────────────────────────────
# SORGENTI DATI
# ─────────────────────────────────────────────
@dataclass
class DataSourceConfig:
    use_overpass: bool   = True
    use_nominatim: bool  = True
    use_cache: bool      = True
    cache_ttl_hours: int = 24

DATA_SOURCES = DataSourceConfig()

# ─────────────────────────────────────────────
# OVERPASS API
# ─────────────────────────────────────────────
@dataclass
class OverpassConfig:
    endpoint: str        = "https://overpass-api.de/api/interpreter"
    timeout_seconds: int = 180
    max_retries: int     = 2
    retry_delay: float   = 3.0
    highway_filters: list = field(default_factory=lambda: [
        "motorway", "trunk", "primary", "secondary", "tertiary",
        "unclassified", "residential", "service",
        "motorway_link", "trunk_link", "primary_link", "secondary_link",
        "tertiary_link", "living_street", "pedestrian", "footway",
        "cycleway", "path",
    ])

OVERPASS = OverpassConfig()

# ─────────────────────────────────────────────
# GRAFO
# ─────────────────────────────────────────────
@dataclass
class GraphConfig:
    directed: bool         = True
    simplify: bool         = True
    add_edge_weights: bool = True

GRAPH = GraphConfig()

# ─────────────────────────────────────────────
# NEO4J
# Legge prima le env var (Docker), poi i default locali.
# ─────────────────────────────────────────────
@dataclass
class Neo4jConfig:
    uri:        str = _env("NEO4J_URI",      "bolt://localhost:7687")
    user:       str = _env("NEO4J_USER",     "neo4j")
    password:   str = _env("NEO4J_PASSWORD", "cityGraph2024!")
    database:   str = _env("NEO4J_DATABASE", "neo4j")
    batch_size: int = _env_int("NEO4J_BATCH_SIZE", 4000)

NEO4J = Neo4jConfig()

# ─────────────────────────────────────────────
# RENDERER (ModernGL + Pygame)
# Solo usato sull'host — ignorato nel container headless.
# ─────────────────────────────────────────────
@dataclass
class RendererConfig:
    window_width: int    = 1400
    window_height: int   = 900
    title: str           = "CityGraph Explorer"
    target_fps: int      = 0
    msaa_samples: int    = 4

    bg_color: tuple          = (0.07, 0.07, 0.11, 1.0)
    road_color: tuple        = (0.31, 0.35, 0.43, 1.0)
    road_highlight: tuple    = (1.00, 0.78, 0.24, 1.0)
    node_color: tuple        = (0.24, 0.47, 0.78, 1.0)
    node_highlight: tuple    = (1.00, 0.31, 0.31, 1.0)
    poi_color: tuple         = (0.31, 0.78, 0.47, 1.0)
    route_start_color: tuple = (0.00, 1.00, 0.39, 1.0)
    text_color: tuple        = (0.86, 0.86, 0.94, 1.0)
    grid_color: tuple        = (30, 30, 46)

    road_width_base: float = 1.2
    max_road_width: float  = 10.0
    route_width: float     = 4.0

    # ── Nodi (punti blu = intersezioni stradali) ──────────────────────────────
    # I nodi rappresentano le intersezioni/giunzioni del grafo OSM.
    # Vengono resi visibili solo da una certa soglia di zoom in avanti
    # per non intasare la vista a scala piccola.
    draw_nodes: bool     = True
    node_radius: int     = 3
    node_zoom_min: float = 10.0   # zoom minimo per visualizzare i nodi (punti blu)

    # ── POI ───────────────────────────────────────────────────────────────────
    draw_pois: bool      = True
    poi_zoom_min: float  = 0.8

RENDERER = RendererConfig()

# ─────────────────────────────────────────────
# CAMERA
# ─────────────────────────────────────────────
@dataclass
class CameraConfig:
    zoom_min: float     = 0.02
    zoom_max: float     = 40.0
    zoom_step: float    = 1.12
    pan_speed: float    = 18.0
    initial_zoom: float = 1.0

CAMERA = CameraConfig()

# ─────────────────────────────────────────────
# LOD (Level Of Detail) — visibilità strade per livello di zoom
#
# Ogni entry definisce: zoom minimo → quali tipi di highway sono visibili.
# Le soglie devono essere in ordine CRESCENTE di zoom.
# A zoom basso si vedono solo le strade principali (come Google Maps).
# Man mano che si avvicina, appaiono le strade secondarie e locali.
#
# Gerarchia highway OSM (dalla più importante alla meno):
#   motorway > trunk > primary > secondary > tertiary >
#   unclassified > residential > service > living_street >
#   pedestrian > footway > cycleway > path
# ─────────────────────────────────────────────
@dataclass
class LodLevel:
    """Un livello LOD: attivo quando camera.zoom >= zoom_min."""
    zoom_min: float
    highway_types: list   # tipi di highway visibili a questa soglia
    label: str            # descrizione leggibile (solo per debug/HUD)

LOD_LEVELS = [
    # Zoom < 0.3: solo autostrade e superstrade (vista satellite regionale)
    LodLevel(
        zoom_min=0.0,
        highway_types=["motorway", "trunk", "motorway_link", "trunk_link"],
        label="Regionale",
    ),
    # Zoom 0.3–1.0: aggiungi strade primarie (vista città)
    LodLevel(
        zoom_min=0.3,
        highway_types=[
            "motorway", "trunk", "primary",
            "motorway_link", "trunk_link", "primary_link",
        ],
        label="Città",
    ),
    # Zoom 1.0–3.0: aggiungi secondarie (vista quartiere)
    LodLevel(
        zoom_min=1.0,
        highway_types=[
            "motorway", "trunk", "primary", "secondary",
            "motorway_link", "trunk_link", "primary_link", "secondary_link",
        ],
        label="Quartiere",
    ),
    # Zoom 3.0–8.0: aggiungi terziarie e non classificate
    LodLevel(
        zoom_min=3.0,
        highway_types=[
            "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
            "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
        ],
        label="Strada",
    ),
    # Zoom >= 8.0: tutto visibile (strade residenziali, servizi, pedonali, ciclabili…)
    LodLevel(
        zoom_min=8.0,
        highway_types=[
            "motorway", "trunk", "primary", "secondary", "tertiary",
            "unclassified", "residential", "service", "living_street",
            "pedestrian", "footway", "cycleway", "path",
            "motorway_link", "trunk_link", "primary_link",
            "secondary_link", "tertiary_link",
        ],
        label="Dettaglio",
    ),
]

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")
