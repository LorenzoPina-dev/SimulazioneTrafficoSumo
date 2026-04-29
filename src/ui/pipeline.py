"""
pipeline.py — Orchestratore: fetch → graph → Neo4j → render (ModernGL) / simulate (SUMO).
"""

import logging
import os
from typing import Optional

import networkx as nx

from config.settings import DEFAULT_CITY, DATA_SOURCES, CACHE_DIR, CityConfig
from fetchers.overpass_fetcher import OverpassFetcher
from fetchers.nominatim_fetcher import NominatimFetcher
from graph.graph_builder import GraphBuilder
from graph.algorithms import GraphAlgorithms
from db.neo4j_manager import Neo4jManager
from renderer.gl_renderer import GLRenderer

log = logging.getLogger(__name__)


class CityPipeline:
    """
    Orchestratore modulare.

    Flusso standard:
      1. fetch_osm_data()
      2. build_graph()
      3. save_graph()       → Neo4j
      4. fetch_pois()       (opzionale)
      5a. render()          → ModernGL + Pygame (visualizzazione grafo)
      5b. simulate()        → SUMO + Pygame     (simulazione traffico)
    """

    def __init__(self, city: Optional[CityConfig] = None):
        self.city     = city or DEFAULT_CITY
        self.osm_raw  = None
        self.G: Optional[nx.DiGraph] = None
        self.pois     = []
        # Percorso del file JSON Overpass in cache — usato da SumoNetExporter
        # per produrre un .net.xml accurato tramite netconvert.
        self._osm_cache_path: Optional[str] = None

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
        # Salva il percorso della cache per passarlo a SumoNetExporter
        self._osm_cache_path = self.overpass.last_cache_path

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
            meta     = self.db.get_metadata()
            db_city  = (meta.get("city") or "").strip().lower()
            req_city = (self.city.name or "").strip().lower()
            if db_city and db_city == req_city:
                log.info("Grafo trovato in Neo4j (città=%s nodi=%s archi=%s)",
                         meta.get("city","?"), meta.get("nodes","?"), meta.get("edges","?"))
                self.G    = self.db.load_graph()
                self.pois = self.db.load_pois()
                # Cerca la cache OSM corrispondente per netconvert
                self._osm_cache_path = self._find_osm_cache()
                return True
            else:
                log.info("Neo4j contiene '%s' ma è richiesta '%s' — skip DB",
                         meta.get("city","?"), self.city.name)
        return False

    def _find_osm_cache(self) -> Optional[str]:
        """
        Cerca il file JSON Overpass in cache più recente per la città corrente.
        Ritorna il percorso o None se non trovato.
        """
        import glob
        pattern = os.path.join(CACHE_DIR, "osm_*.json")
        files   = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if files:
            log.info("Cache OSM trovata per netconvert: %s", files[0])
            return files[0]
        log.info("Nessuna cache OSM trovata — netconvert userà i dati del grafo")
        return None

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
    # STEP 5a: RENDER (ModernGL)
    # ──────────────────────────────────────────

    def render(self) -> None:
        log.info("═══ STEP 5a: Avvio GLRenderer ═══")
        if self.G is None:
            raise RuntimeError("Grafo non disponibile.")
        renderer = GLRenderer(self.G, pois=self.pois)
        renderer.run()

    # ──────────────────────────────────────────
    # STEP 5b: SIMULATE (SUMO + Pygame)
    # ──────────────────────────────────────────

    def simulate(self) -> None:
        """
        Avvia il renderer interattivo SUMO.
        - Passa osm_cache_path a SumoNetExporter per usare netconvert
          con i dati OSM grezzi originali (proiezioni corrette).
        - Richiede SUMO_HOME impostato nell'ambiente.
        """
        log.info("═══ STEP 5b: Avvio SumoRenderer ═══")
        if self.G is None:
            raise RuntimeError("Grafo non disponibile.")

        from sumo.sumo_renderer import SumoRenderer

        # data_dir relativo alla root del progetto (non a src/)
        src_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(src_dir, "data", "sumo")

        renderer = SumoRenderer(
            self.G,
            data_dir=data_dir,
            osm_cache_path=self._osm_cache_path,
        )
        renderer.run()

    # ──────────────────────────────────────────
    # SUBGRAPH DA BBOX
    # ──────────────────────────────────────────

    def load_subgraph_for_area(self, area_query: str) -> Optional[nx.DiGraph]:
        log.info("Caricamento sottografo per area: %s", area_query)
        bbox = self.nominatim.get_city_bbox(area_query)
        if not bbox:
            log.warning("BBox non trovata per area '%s'", area_query)
            return None
        lat_min, lat_max, lon_min, lon_max = bbox
        try:
            self.db.connect()
        except Exception as exc:
            log.warning("Neo4j non raggiungibile: %s", exc)
            return None
        return self.db.load_subgraph_by_bbox(lon_min, lat_min, lon_max, lat_max)

    # ──────────────────────────────────────────
    # SCORCIATOIA
    # ──────────────────────────────────────────

    def run_full(self, force_refetch: bool = False, mode: str = "render") -> None:
        loaded = False
        if not force_refetch:
            loaded = self.load_graph_from_db()

        if not loaded:
            self.fetch_osm_data()
            self.build_graph()
            self.save_graph()
            self.fetch_pois()

        if mode == "simulate":
            self.simulate()
        else:
            self.render()

        self.db.close()
