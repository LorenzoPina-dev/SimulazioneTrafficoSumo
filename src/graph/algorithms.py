"""
algorithms.py — Algoritmi sul grafo: shortest path, isocrone, analisi.
"""

import logging
from typing import Optional, List, Dict, Any

import networkx as nx

log = logging.getLogger(__name__)


class GraphAlgorithms:
    """
    Raccoglie gli algoritmi applicabili al grafo città.
    Tutti i metodi sono stateless e accettano il grafo come parametro.
    """

    # ──────────────────────────────────────────
    # SHORTEST PATH
    # ──────────────────────────────────────────

    @staticmethod
    def shortest_path(
        G: nx.DiGraph,
        source: int,
        target: int,
        weight: str = "weight",
    ) -> Optional[List[int]]:
        """
        Dijkstra dal nodo source al nodo target.
        Restituisce lista di node_id o None se non raggiungibile.
        """
        try:
            path = nx.shortest_path(G, source, target, weight=weight)
            return path
        except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
            log.warning("Nessun percorso da %s a %s: %s", source, target, exc)
            return None

    @staticmethod
    def shortest_path_length(
        G: nx.DiGraph, source: int, target: int, weight: str = "weight"
    ) -> Optional[float]:
        try:
            return nx.shortest_path_length(G, source, target, weight=weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    # ──────────────────────────────────────────
    # NODO PIÙ VICINO
    # ──────────────────────────────────────────

    @staticmethod
    def nearest_node(G: nx.DiGraph, lat: float, lon: float) -> Optional[int]:
        """
        Trova il nodo del grafo più vicino alle coordinate date
        usando distanza euclidea semplificata (ok per piccole aree).
        """
        best_node = None
        best_dist = float("inf")
        for nid, data in G.nodes(data=True):
            if "lat" not in data or "lon" not in data:
                continue
            d = (data["lat"] - lat) ** 2 + (data["lon"] - lon) ** 2
            if d < best_dist:
                best_dist = d
                best_node = nid
        return best_node

    # ──────────────────────────────────────────
    # STATISTICHE GRAFO
    # ──────────────────────────────────────────

    @staticmethod
    def graph_stats(G: nx.DiGraph) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
        }
        try:
            if isinstance(G, nx.DiGraph):
                wcc = nx.number_weakly_connected_components(G)
                scc = nx.number_strongly_connected_components(G)
                stats["weakly_connected_components"] = wcc
                stats["strongly_connected_components"] = scc
            else:
                stats["connected_components"] = nx.number_connected_components(G)
        except Exception:
            pass

        total_len = sum(
            d.get("length_m", 0) for _, _, d in G.edges(data=True)
        )
        stats["total_road_length_km"] = round(total_len / 1000, 2)
        return stats

    # ──────────────────────────────────────────
    # NODI CENTRALI (betweenness approssimato)
    # ──────────────────────────────────────────

    @staticmethod
    def top_central_nodes(
        G: nx.DiGraph, k: int = 10, sample: int = 200
    ) -> List[Dict]:
        """
        Betweenness centrality approssimata su un campione di nodi.
        Ritorna lista di dict {node_id, score, lat, lon}.
        """
        try:
            centrality = nx.betweenness_centrality(
                G, k=min(sample, G.number_of_nodes()), weight="weight", normalized=True
            )
            top = sorted(centrality.items(), key=lambda x: x[1], reverse=True)[:k]
            result = []
            for nid, score in top:
                data = G.nodes[nid]
                result.append({
                    "node_id": nid,
                    "score": round(score, 6),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                })
            return result
        except Exception as exc:
            log.warning("Betweenness fallita: %s", exc)
            return []
