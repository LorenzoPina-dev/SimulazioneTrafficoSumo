"""
pipeline.py — Orchestratore: fetch → graph → Neo4j → render (ModernGL).
"""

import logging
from typing import Optional

import networkx as nx

from config.settings import DEFAULT_CITY, DATA_SOURCES, CityConfig
from fetchers.overpass_fetcher import OverpassFetcher
from fetchers.nominatim_fetcher import NominatimFetcher
from graph.graph_builder import GraphBuilder
from graph.algorithms import GraphAlgorithms
from db.neo4j_manager import Neo4jManager
from renderer.gl_renderer import GLRenderer

log = logging.getLogger(__name__)


class CityPipeline:
    """
    Orchestratore modulare. Ogni step è un metodo separato e sostituibile.

    Flusso standard:
      1. fetch_osm_data()
      2. build_graph()
      3. save_graph()       → Neo4j
      4. fetch_pois()       (opzionale)
      5. render()           → ModernGL + Pygame
    """

    def __init__(self, city: Optional[CityConfig] = None):
        self.city    = city or DEFAULT_CITY
        self.osm_raw = None
        self.G: Optional[nx.DiGraph] = None
        self.pois    = []

        self.overpass  = OverpassFetcher()
        self.nominatim = NominatimFetcher()
        self.builder   = GraphBuilder()
        self.db        = Neo4jManager()

    # ──────────────────────────────────────────
    # STEP 1: FETCH OSM
    # ──────────────────────────────────────────

    def fetch_osm_data(self) -> None:
        log.info("═══ STEP 1: Fetch dati OSM per '%s' ═══", self.city.osm_area_query)
        self.osm_raw = self.overpass.fetch_road_network(self.city)
        log.info("Ricevuti %d elementi OSM", len(self.osm_raw.get("elements", [])))

    # ──────────────────────────────────────────
    # STEP 2: BUILD GRAPH
    # ──────────────────────────────────────────

    def build_graph(self) -> None:
        log.info("═══ STEP 2: Costruzione grafo ═══")
        if self.osm_raw is None:
            raise RuntimeError("Dati OSM non disponibili.")
        self.G = self.builder.build(self.osm_raw)
        log.info("Grafo: %s", GraphAlgorithms.graph_stats(self.G))

    # ──────────────────────────────────────────
    # STEP 3: PERSIST → NEO4J
    # ──────────────────────────────────────────

    def save_graph(self) -> None:
        log.info("═══ STEP 3: Salvataggio Neo4j ═══")
        if self.G is None:
            raise RuntimeError("Grafo non costruito.")
        self.db.connect()
        self.db.save_graph(self.G, city_name=self.city.name)

    def load_graph_from_db(self) -> bool:
        """Carica il grafo da Neo4j se esiste. Ritorna True se caricato."""
        try:
            self.db.connect()
        except Exception as exc:
            log.warning("Neo4j non raggiungibile: %s — uso solo cache OSM.", exc)
            return False

        if self.db.graph_exists():
            meta = self.db.get_metadata()
            log.info("Grafo trovato in Neo4j (città=%s nodi=%s archi=%s)",
                     meta.get("city", "?"), meta.get("nodes", "?"), meta.get("edges", "?"))
            self.G    = self.db.load_graph()
            self.pois = self.db.load_pois()
            return True
        return False

    # ──────────────────────────────────────────
    # STEP 4: POI
    # ──────────────────────────────────────────

    def fetch_pois(self) -> None:
        if not DATA_SOURCES.use_nominatim:
            return
        log.info("═══ STEP 4: Fetch POI ═══")
        self.pois = self.nominatim.fetch_pois(self.city.osm_area_query)
        if self.db._driver:
            self.db.save_pois(self.pois)
        log.info("POI trovati: %d", len(self.pois))

    # ──────────────────────────────────────────
    # STEP 5: RENDER (ModernGL)
    # ──────────────────────────────────────────

    def render(self) -> None:
        log.info("═══ STEP 5: Avvio GLRenderer ═══")
        if self.G is None:
            raise RuntimeError("Grafo non disponibile.")
        renderer = GLRenderer(self.G, pois=self.pois)
        renderer.run()

    # ──────────────────────────────────────────
    # SCORCIATOIA
    # ──────────────────────────────────────────

    def run_full(self, force_refetch: bool = False) -> None:
        loaded = False
        if not force_refetch:
            loaded = self.load_graph_from_db()

        if not loaded:
            self.fetch_osm_data()
            self.build_graph()
            self.save_graph()
            self.fetch_pois()

        self.render()
        self.db.close()
