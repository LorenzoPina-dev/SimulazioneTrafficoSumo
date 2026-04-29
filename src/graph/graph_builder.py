import math
import logging
import networkx as nx
from typing import Dict, Any, List, Tuple

log = logging.getLogger(__name__)

PRIORITY_MAP = {
    'motorway': 10, 'trunk': 9, 'primary': 8, 'secondary': 7,
    'tertiary': 6, 'residential': 4, 'living_street': 3, 'service': 2, 'unclassified': 2
}

class GraphBuilder:
    def __init__(self):
        self.directed = True 

    def build(self, osm_data: Dict[str, Any]) -> nx.DiGraph:
        elements = osm_data.get("elements", [])
        nodes_raw, ways = self._separate_elements(elements)
        
        # 1. Conta quante vie passano per ogni nodo
        node_usage = self._count_node_usage(ways)
        
        G = nx.DiGraph()
        
        # 2. Identifica i nodi che sono "VERI" incroci
        # Un nodo è un vero incrocio se:
        # - È l'inizio o la fine di una via
        # - È usato da 2 o più vie diverse
        # - Ha tag speciali (stop, semaforo)
        self._add_essential_nodes(G, nodes_raw, node_usage, ways)
        
        # 3. Collega i nodi essenziali creando archi "collassati"
        # Questo trasforma i nodi intermedi in semplici segmenti di un unico arco
        self._add_collapsed_edges(G, ways, nodes_raw)

        log.info("Grafo Ottimizzato: %d nodi (incroci reali), %d archi", G.number_of_nodes(), G.number_of_edges())
        return G

    def _separate_elements(self, elements: List[Dict]) -> Tuple[Dict, List[Dict]]:
        nodes = {el["id"]: el for el in elements if el["type"] == "node"}
        ways = [el for el in elements if el["type"] == "way" and "tags" in el]
        return nodes, ways

    def _count_node_usage(self, ways: List[Dict]) -> Dict[int, int]:
        counts = {}
        for way in ways:
            for node_id in way.get("nodes", []):
                counts[node_id] = counts.get(node_id, 0) + 1
        return counts

    def _add_essential_nodes(self, G: nx.DiGraph, nodes_raw: Dict, node_usage: Dict, ways: List[Dict]):
        essential_ids = set()
        for way in ways:
            nodes = way.get("nodes", [])
            if not nodes: continue
            
            # Estremi della via sono sempre nodi nel grafo
            essential_ids.add(nodes[0])
            essential_ids.add(nodes[-1])
            
            for nid in nodes:
                # Se il nodo è un incrocio tra vie diverse, è essenziale
                if node_usage.get(nid, 0) > 1:
                    essential_ids.add(nid)
                # Se il nodo ha tag di controllo traffico, è essenziale
                if nid in nodes_raw and "tags" in nodes_raw[nid]:
                    t = nodes_raw[nid]["tags"]
                    if any(k in t for k in ["highway", "amenity", "junction"]):
                        if t.get("highway") in ["stop", "traffic_signals", "give_way"]:
                            essential_ids.add(nid)

        for nid in essential_ids:
            if nid in nodes_raw:
                n = nodes_raw[nid]
                tags = n.get("tags", {})
                G.add_node(
                    nid,
                    lat=n["lat"], lon=n["lon"],
                    osmid=nid,
                    node_type=self._guess_node_type(tags)
                )

    def _guess_node_type(self, tags: Dict) -> str:
        hw = tags.get("highway", "")
        if hw == "traffic_signals": return "traffic_light"
        if hw == "stop": return "priority_stop"
        return "priority"

    def _add_collapsed_edges(self, G: nx.DiGraph, ways: List[Dict], nodes_raw: Dict):
        """Crea archi diretti tra i nodi essenziali, ignorando i nodi intermedi."""
        for way in ways:
            tags = way.get("tags", {})
            hw = tags.get("highway", "residential")
            
            is_roundabout = tags.get("junction") == "roundabout" or tags.get("highway") == "roundabout"
            oneway_tag = str(tags.get("oneway", "no")).lower()
            is_oneway = is_roundabout or oneway_tag in ("yes", "true", "1", "-1")
            
            priority = PRIORITY_MAP.get(hw, 2)
            if is_roundabout: priority += 2

            nodes = way.get("nodes", [])
            if len(nodes) < 2: continue

            # Partiamo dal primo nodo della via
            start_node = nodes[0]
            accumulated_dist = 0

            for i in range(1, len(nodes)):
                u_id, v_id = nodes[i-1], nodes[i]
                if u_id not in nodes_raw or v_id not in nodes_raw: continue
                
                accumulated_dist += self._haversine(nodes_raw[u_id], nodes_raw[v_id])

                # Se il nodo corrente è un nodo "ESSENZIALE" (incrocio reale), chiudiamo l'arco
                if v_id in G.nodes:
                    edge_attrs = {
                        "osmid": way["id"],
                        "priority": priority,
                        "length_m": round(accumulated_dist, 2),
                        "speed_kmh": self._parse_maxspeed(tags),
                        "allow_uturn": False,
                        "junction": "roundabout" if is_roundabout else "",
                        "oneway": True
                    }

                    # Direzione di marcia
                    if oneway_tag == "-1":
                        G.add_edge(v_id, start_node, **edge_attrs)
                    else:
                        G.add_edge(start_node, v_id, **edge_attrs)
                        if not is_oneway:
                            G.add_edge(v_id, start_node, **edge_attrs)

                    # Reset per il prossimo segmento tra incroci
                    start_node = v_id
                    accumulated_dist = 0

    @staticmethod
    def _haversine(n1: Dict, n2: Dict) -> float:
        R = 6371000
        phi1, phi2 = math.radians(n1["lat"]), math.radians(n2["lat"])
        dphi = math.radians(n2["lat"] - n1["lat"])
        dlam = math.radians(n2["lon"] - n1["lon"])
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
        return 2 * R * math.asin(math.sqrt(a))

    @staticmethod
    def _parse_maxspeed(tags: Dict) -> int:
        raw = tags.get("maxspeed", "50")
        try: return int(raw.split()[0])
        except: return 50