"""
graph_builder.py — Converte i dati OSM grezzi in un grafo NetworkX.
"""

import math
import logging
from typing import Dict, Any, List, Optional, Tuple

import networkx as nx

from config.settings import GRAPH, OVERPASS

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# COSTANTI GEOGRAFICHE
# ─────────────────────────────────────────────
EARTH_RADIUS_M = 6_371_000  # metri


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza in metri tra due coordinate geografiche."""
    r = EARTH_RADIUS_M
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class GraphBuilder:
    """
    Trasforma gli elementi JSON di Overpass in un grafo NetworkX.

    Nodi  → osmid, lat, lon, (+ metadati opzionali)
    Archi → osmid, highway, name, length_m, oneway, maxspeed, ...
    """

    def build(self, osm_data: Dict[str, Any]) -> nx.DiGraph:
        """
        Punto di ingresso principale.
        Restituisce un DiGraph (diretto) o Graph (non diretto) secondo la config.
        """
        elements = osm_data.get("elements", [])

        nodes_raw, ways = self._separate_elements(elements)

        log.info("OSM: %d nodi, %d ways", len(nodes_raw), len(ways))

        G = nx.DiGraph() if GRAPH.directed else nx.Graph()
        self._add_nodes(G, nodes_raw)
        self._add_edges(G, ways, nodes_raw)

        if GRAPH.simplify:
            G = self._simplify_graph(G)
            log.info("Grafo semplificato: %d nodi, %d archi", G.number_of_nodes(), G.number_of_edges())
        else:
            log.info("Grafo: %d nodi, %d archi", G.number_of_nodes(), G.number_of_edges())

        return G

    # ──────────────────────────────────────────
    # PARSING ELEMENTI OSM
    # ──────────────────────────────────────────

    @staticmethod
    def _separate_elements(elements: List[Dict]) -> Tuple[Dict, List[Dict]]:
        nodes = {}
        ways  = []
        for el in elements:
            t = el.get("type")
            if t == "node":
                nodes[el["id"]] = el
            elif t == "way":
                ways.append(el)
        return nodes, ways

    # ──────────────────────────────────────────
    # AGGIUNTA NODI
    # ──────────────────────────────────────────

    @staticmethod
    def _add_nodes(G: nx.DiGraph, nodes_raw: Dict) -> None:
        for nid, n in nodes_raw.items():
            tags = n.get("tags", {})
            G.add_node(
                nid,
                lat=n["lat"],
                lon=n["lon"],
                name=tags.get("name", ""),
                amenity=tags.get("amenity", ""),
                highway=tags.get("highway", ""),
            )

    # ──────────────────────────────────────────
    # AGGIUNTA ARCHI
    # ──────────────────────────────────────────

    def _add_edges(self, G: nx.DiGraph, ways: List[Dict], nodes_raw: Dict) -> None:
        for way in ways:
            tags   = way.get("tags", {})
            refs   = way.get("nodes", [])
            hw     = tags.get("highway", "")
            name   = tags.get("name", "")
            oneway = self._parse_oneway(tags)
            maxspeed = self._parse_maxspeed(tags)

            # Filtra highway non desiderati
            if hw not in OVERPASS.highway_filters:
                continue

            for i in range(len(refs) - 1):
                u, v = refs[i], refs[i + 1]
                if u not in nodes_raw or v not in nodes_raw:
                    continue

                nu, nv = nodes_raw[u], nodes_raw[v]
                dist = haversine(nu["lat"], nu["lon"], nv["lat"], nv["lon"])

                edge_attrs = dict(
                    osmid=way["id"],
                    highway=hw,
                    name=name,
                    length_m=round(dist, 2),
                    weight=round(dist, 2),   # usato dagli algoritmi
                    oneway=oneway,
                    maxspeed=maxspeed,
                )

                G.add_edge(u, v, **edge_attrs)
                if not oneway and GRAPH.directed:
                    G.add_edge(v, u, **edge_attrs)

    # ──────────────────────────────────────────
    # SEMPLIFICAZIONE
    # ──────────────────────────────────────────

    @staticmethod
    def _simplify_graph(G: nx.DiGraph) -> nx.DiGraph:
        """
        Rimuove nodi di passaggio (grado = 2, un solo arco in/out)
        collegando direttamente i nodi terminali.
        Conserva i nodi alle intersezioni e ai capolinea.
        """
        to_remove = []
        for node in list(G.nodes):
            preds = list(G.predecessors(node))
            succs = list(G.successors(node))
            # Nodo intermedio: 1 predecessore, 1 successore, diversi
            if len(preds) == 1 and len(succs) == 1 and preds[0] != succs[0]:
                p, s = preds[0], succs[0]
                ep  = G[p][node]
                es  = G[node][s]
                new_len = ep.get("length_m", 0) + es.get("length_m", 0)
                merged = dict(ep)
                merged["length_m"] = round(new_len, 2)
                merged["weight"]   = round(new_len, 2)
                G.add_edge(p, s, **merged)
                if not ep.get("oneway") and isinstance(G, nx.DiGraph):
                    G.add_edge(s, p, **merged)
                to_remove.append(node)

        G.remove_nodes_from(to_remove)
        return G

    # ──────────────────────────────────────────
    # HELPER PARSING TAG OSM
    # ──────────────────────────────────────────

    @staticmethod
    def _parse_oneway(tags: Dict) -> bool:
        v = tags.get("oneway", "no").lower()
        return v in ("yes", "true", "1", "-1")

    @staticmethod
    def _parse_maxspeed(tags: Dict) -> Optional[int]:
        raw = tags.get("maxspeed", "")
        if not raw:
            return None
        try:
            return int(raw.split()[0])
        except (ValueError, IndexError):
            return None
