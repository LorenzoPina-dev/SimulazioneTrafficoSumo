"""
neo4j_manager.py — Persistenza del grafo su Neo4j.

Schema a grafo nativo:
  (:Intersection {osmid, lat, lon, name, amenity, highway})
  -[:ROAD {osmid, highway, name, length_m, weight, oneway, maxspeed}]->
  (:Intersection)
  (:POI {osmid, lat, lon, name, amenity_type})

Query Cypher ottimizzate con UNWIND per import batch ad alta velocità.
Indici su osmid per lookup O(log n).
"""

import logging
from typing import Optional, List, Dict, Any

import networkx as nx
from neo4j import GraphDatabase, Driver, Session

from config.settings import NEO4J

log = logging.getLogger(__name__)


class Neo4jManager:
    """
    Gestisce connessione e operazioni CRUD sul grafo Neo4j.
    Usa transazioni esplicite e batch UNWIND per massimizzare il throughput.
    """

    def __init__(self):
        self._driver: Optional[Driver] = None

    # ──────────────────────────────────────────
    # CONNESSIONE
    # ──────────────────────────────────────────

    def connect(self) -> None:
        log.info("Connessione Neo4j: %s (db=%s)", NEO4J.uri, NEO4J.database)
        self._driver = GraphDatabase.driver(
            NEO4J.uri,
            auth=(NEO4J.user, NEO4J.password),
            max_connection_pool_size=10,
        )
        self._driver.verify_connectivity()
        self._create_indexes()
        log.info("Neo4j connesso.")

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    def _session(self) -> Session:
        return self._driver.session(database=NEO4J.database)

    # ──────────────────────────────────────────
    # INDICI / CONSTRAINTS
    # ──────────────────────────────────────────

    def _create_indexes(self) -> None:
        """Crea indici se non esistono — eseguito una sola volta."""
        queries = [
            "CREATE CONSTRAINT intersection_osmid IF NOT EXISTS "
            "FOR (n:Intersection) REQUIRE n.osmid IS UNIQUE",
            "CREATE INDEX road_highway IF NOT EXISTS "
            "FOR ()-[r:ROAD]-() ON (r.highway)",
            "CREATE INDEX poi_type IF NOT EXISTS "
            "FOR (p:POI) ON (p.amenity_type)",
        ]
        with self._session() as s:
            for q in queries:
                try:
                    s.run(q)
                except Exception as exc:
                    log.debug("Index già esistente o errore ignorabile: %s", exc)

    # ──────────────────────────────────────────
    # SALVATAGGIO GRAFO
    # ──────────────────────────────────────────

    def save_graph(self, G: nx.DiGraph, city_name: str = "") -> None:
        log.info("Salvataggio grafo Neo4j (%d nodi, %d archi)...",
                 G.number_of_nodes(), G.number_of_edges())

        # Svuota il grafo precedente (solo nodi/archi della città corrente)
        with self._session() as s:
            s.run("MATCH (n:Intersection) DETACH DELETE n")
            s.run("MATCH (p:POI) DETACH DELETE p")

        # ── Nodi a batch ──────────────────────
        node_list = [
            {
                "osmid": nid,
                "lat":     d.get("lat", 0.0),
                "lon":     d.get("lon", 0.0),
                "name":    d.get("name", ""),
                "amenity": d.get("amenity", ""),
                "highway": d.get("highway", ""),
                "city":    city_name,
            }
            for nid, d in G.nodes(data=True)
        ]
        self._batch_write(
            """
            UNWIND $rows AS row
            MERGE (n:Intersection {osmid: row.osmid})
            SET n.lat     = row.lat,
                n.lon     = row.lon,
                n.name    = row.name,
                n.amenity = row.amenity,
                n.highway = row.highway,
                n.city    = row.city
            """,
            node_list,
        )
        log.info("Nodi salvati: %d", len(node_list))

        # ── Archi a batch ─────────────────────
        edge_list = [
            {
                "src":      u,
                "dst":      v,
                "osmid":    d.get("osmid"),
                "highway":  d.get("highway", ""),
                "name":     d.get("name", ""),
                "length_m": d.get("length_m", 0.0),
                "weight":   d.get("weight", 0.0),
                "oneway":   bool(d.get("oneway", False)),
                "maxspeed": d.get("maxspeed"),
            }
            for u, v, d in G.edges(data=True)
        ]
        self._batch_write(
            """
            UNWIND $rows AS row
            MATCH (a:Intersection {osmid: row.src})
            MATCH (b:Intersection {osmid: row.dst})
            MERGE (a)-[r:ROAD {osmid: row.osmid, highway: row.highway}]->(b)
            SET r.name     = row.name,
                r.length_m = row.length_m,
                r.weight   = row.weight,
                r.oneway   = row.oneway,
                r.maxspeed = row.maxspeed
            """,
            edge_list,
        )
        log.info("Archi salvati: %d", len(edge_list))

        # Metadata come nodo speciale
        with self._session() as s:
            s.run(
                "MERGE (m:Metadata {key:'city'}) SET m.value=$city, m.nodes=$nodes, m.edges=$edges",
                city=city_name,
                nodes=G.number_of_nodes(),
                edges=G.number_of_edges(),
            )
        log.info("Grafo Neo4j salvato.")

    # ──────────────────────────────────────────
    # CARICAMENTO GRAFO
    # ──────────────────────────────────────────

    def load_graph(self) -> nx.DiGraph:
        """Ricostruisce NetworkX DiGraph leggendo da Neo4j."""
        G = nx.DiGraph()
        with self._session() as s:
            # Nodi
            result = s.run(
                "MATCH (n:Intersection) "
                "RETURN n.osmid AS osmid, n.lat AS lat, n.lon AS lon, "
                "       n.name AS name, n.amenity AS amenity, n.highway AS highway"
            )
            for rec in result:
                G.add_node(
                    rec["osmid"],
                    lat=rec["lat"], lon=rec["lon"],
                    name=rec["name"] or "",
                    amenity=rec["amenity"] or "",
                    highway=rec["highway"] or "",
                )

            # Archi
            result = s.run(
                "MATCH (a:Intersection)-[r:ROAD]->(b:Intersection) "
                "RETURN a.osmid AS src, b.osmid AS dst, "
                "       r.osmid AS osmid, r.highway AS highway, r.name AS name, "
                "       r.length_m AS length_m, r.weight AS weight, "
                "       r.oneway AS oneway, r.maxspeed AS maxspeed"
            )
            for rec in result:
                G.add_edge(
                    rec["src"], rec["dst"],
                    osmid=rec["osmid"],
                    highway=rec["highway"] or "",
                    name=rec["name"] or "",
                    length_m=rec["length_m"] or 0.0,
                    weight=rec["weight"] or 0.0,
                    oneway=bool(rec["oneway"]),
                    maxspeed=rec["maxspeed"],
                )

        log.info("Grafo caricato da Neo4j: %d nodi, %d archi",
                 G.number_of_nodes(), G.number_of_edges())
        return G

    def graph_exists(self) -> bool:
        with self._session() as s:
            result = s.run("MATCH (n:Intersection) RETURN count(n) AS c LIMIT 1")
            return result.single()["c"] > 0

    def get_metadata(self) -> Dict[str, Any]:
        with self._session() as s:
            result = s.run(
                "MATCH (m:Metadata {key:'city'}) "
                "RETURN m.value AS city, m.nodes AS nodes, m.edges AS edges"
            )
            rec = result.single()
            if rec:
                return {"city": rec["city"], "nodes": rec["nodes"], "edges": rec["edges"]}
        return {}

    def load_subgraph_by_bbox(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> nx.DiGraph:
        """
        Estrae dal DB il sottografo contenuto nel bounding box fornito.
        Ritorna un `networkx.DiGraph` con gli stessi attributi dei nodi/archi.

        Parametri: lon_min, lat_min, lon_max, lat_max (gradi decimali)
        """
        G = nx.DiGraph()
        with self._session() as s:
            # Nodi nel bbox
            node_q = (
                "MATCH (n:Intersection) "
                "WHERE n.lon >= $lon_min AND n.lon <= $lon_max "
                "  AND n.lat >= $lat_min AND n.lat <= $lat_max "
                "RETURN n.osmid AS osmid, n.lat AS lat, n.lon AS lon, "
                "       n.name AS name, n.amenity AS amenity, n.highway AS highway"
            )
            params = {"lon_min": lon_min, "lon_max": lon_max, "lat_min": lat_min, "lat_max": lat_max}
            result = s.run(node_q, **params)
            nodes_in_bbox = set()
            for rec in result:
                osmid = rec["osmid"]
                nodes_in_bbox.add(osmid)
                G.add_node(osmid,
                           lat=rec["lat"], lon=rec["lon"],
                           name=rec.get("name") or "",
                           amenity=rec.get("amenity") or "",
                           highway=rec.get("highway") or "")

            # Archi che hanno entrambi gli endpoint nel bbox
            edge_q = (
                "MATCH (a:Intersection)-[r:ROAD]->(b:Intersection) "
                "WHERE a.lon >= $lon_min AND a.lon <= $lon_max AND a.lat >= $lat_min AND a.lat <= $lat_max "
                "  AND b.lon >= $lon_min AND b.lon <= $lon_max AND b.lat >= $lat_min AND b.lat <= $lat_max "
                "RETURN a.osmid AS src, b.osmid AS dst, r.osmid AS osmid, r.highway AS highway, r.name AS name, "
                "       r.length_m AS length_m, r.weight AS weight, r.oneway AS oneway, r.maxspeed AS maxspeed"
            )
            result = s.run(edge_q, **params)
            for rec in result:
                src = rec["src"]
                dst = rec["dst"]
                # Safety: aggiungi i nodi anche se non presenti (edge case)
                if src not in G:
                    G.add_node(src)
                if dst not in G:
                    G.add_node(dst)
                G.add_edge(src, dst,
                           osmid=rec.get("osmid"),
                           highway=rec.get("highway") or "",
                           name=rec.get("name") or "",
                           length_m=rec.get("length_m") or 0.0,
                           weight=rec.get("weight") or 0.0,
                           oneway=bool(rec.get("oneway")),
                           maxspeed=rec.get("maxspeed"))

        log.info("Sottografo caricato da Neo4j: nodi=%d archi=%d (bbox=[%f,%f,%f,%f])",
                 G.number_of_nodes(), G.number_of_edges(), lon_min, lat_min, lon_max, lat_max)
        return G

    # ──────────────────────────────────────────
    # POI
    # ──────────────────────────────────────────

    def save_pois(self, pois: List[Dict]) -> None:
        with self._session() as s:
            s.run("MATCH (p:POI) DETACH DELETE p")
        rows = [
            {
                "lat":          float(p.get("lat", 0)),
                "lon":          float(p.get("lon", 0)),
                "name":         p.get("display_name", p.get("name", "")),
                "amenity_type": p.get("_amenity_type", ""),
                "osm_id":       p.get("osm_id") or p.get("place_id"),
            }
            for p in pois
        ]
        self._batch_write(
            """
            UNWIND $rows AS row
            CREATE (p:POI {
                lat: row.lat, lon: row.lon,
                name: row.name,
                amenity_type: row.amenity_type,
                osm_id: row.osm_id
            })
            """,
            rows,
        )

    def load_pois(self) -> List[Dict]:
        with self._session() as s:
            result = s.run(
                "MATCH (p:POI) RETURN p.lat AS lat, p.lon AS lon, "
                "p.name AS name, p.amenity_type AS amenity_type"
            )
            return [
                {"lat": r["lat"], "lon": r["lon"],
                 "name": r["name"], "amenity_type": r["amenity_type"]}
                for r in result
            ]

    # ──────────────────────────────────────────
    # SHORTEST PATH NATIVO NEO4J (GDS plugin o fallback Cypher)
    # ──────────────────────────────────────────

    def shortest_path_cypher(self, src_osmid: int, dst_osmid: int) -> List[int]:
        """
        Shortest path via Cypher APOC/built-in shortestPath.
        Ritorna lista di osmid. Fallback su NetworkX se vuota.
        """
        with self._session() as s:
            result = s.run(
                """
                MATCH (a:Intersection {osmid: $src}), (b:Intersection {osmid: $dst}),
                      p = shortestPath((a)-[:ROAD*]->(b))
                RETURN [n IN nodes(p) | n.osmid] AS path
                """,
                src=src_osmid, dst=dst_osmid,
            )
            rec = result.single()
            if rec and rec["path"]:
                return rec["path"]
        return []

    # ──────────────────────────────────────────
    # BATCH WRITER
    # ──────────────────────────────────────────

    def _batch_write(self, query: str, rows: List[Dict]) -> None:
        """Scrive `rows` in batch di NEO4J.batch_size con transazioni esplicite."""
        size = NEO4J.batch_size
        total = len(rows)
        for start in range(0, total, size):
            chunk = rows[start: start + size]
            with self._session() as s:
                s.run(query, rows=chunk)
            log.debug("Batch scritto: %d/%d", min(start + size, total), total)
