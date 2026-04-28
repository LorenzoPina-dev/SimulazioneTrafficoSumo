"""
db_manager.py — Persistenza del grafo su SQLite (schema relazionale).
Progettato per essere sostituibile con Neo4j o ArangoDB cambiando solo questo file.
"""

import sqlite3
import logging
import os
from typing import Optional, List, Dict, Any, Tuple

import networkx as nx

from config.settings import DB

log = logging.getLogger(__name__)


class DBManager:
    """
    Gestisce la persistenza del grafo su SQLite.
    Schema:
      - nodes(id, lat, lon, name, amenity, highway)
      - edges(id, source, target, osmid, highway, name, length_m, weight, oneway, maxspeed)
      - pois(id, lat, lon, name, amenity_type, osm_id)
      - metadata(key, value)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.path = db_path or DB.path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    # ──────────────────────────────────────────
    # CONNESSIONE
    # ──────────────────────────────────────────

    def connect(self) -> None:
        log.info("Connessione DB: %s", self.path)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # ──────────────────────────────────────────
    # SCHEMA
    # ──────────────────────────────────────────

    def _create_schema(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS nodes (
            id       INTEGER PRIMARY KEY,
            lat      REAL NOT NULL,
            lon      REAL NOT NULL,
            name     TEXT DEFAULT '',
            amenity  TEXT DEFAULT '',
            highway  TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS edges (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            source    INTEGER NOT NULL,
            target    INTEGER NOT NULL,
            osmid     INTEGER,
            highway   TEXT,
            name      TEXT,
            length_m  REAL,
            weight    REAL,
            oneway    INTEGER DEFAULT 0,
            maxspeed  INTEGER,
            UNIQUE(source, target)
        );
        CREATE TABLE IF NOT EXISTS pois (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            lat          REAL,
            lon          REAL,
            name         TEXT,
            amenity_type TEXT,
            osm_id       INTEGER
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        """
        self._conn.executescript(sql)
        self._conn.commit()

    # ──────────────────────────────────────────
    # SALVATAGGIO GRAFO
    # ──────────────────────────────────────────

    def save_graph(self, G: nx.DiGraph, city_name: str = "") -> None:
        """Salva (o sovrascrive) l'intero grafo nel DB."""
        c = self._conn
        log.info("Salvataggio grafo nel DB (%d nodi, %d archi)...",
                 G.number_of_nodes(), G.number_of_edges())

        # Pulisce le tabelle
        c.execute("DELETE FROM edges")
        c.execute("DELETE FROM nodes")

        # Nodi
        node_rows = [
            (nid,
             d.get("lat", 0.0),
             d.get("lon", 0.0),
             d.get("name", ""),
             d.get("amenity", ""),
             d.get("highway", ""))
            for nid, d in G.nodes(data=True)
        ]
        c.executemany(
            "INSERT OR REPLACE INTO nodes(id,lat,lon,name,amenity,highway) VALUES(?,?,?,?,?,?)",
            node_rows,
        )

        # Archi
        edge_rows = [
            (u, v,
             d.get("osmid"),
             d.get("highway", ""),
             d.get("name", ""),
             d.get("length_m", 0.0),
             d.get("weight", 0.0),
             int(d.get("oneway", False)),
             d.get("maxspeed"))
            for u, v, d in G.edges(data=True)
        ]
        c.executemany(
            """INSERT OR REPLACE INTO edges
               (source,target,osmid,highway,name,length_m,weight,oneway,maxspeed)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            edge_rows,
        )

        # Metadata
        c.execute("INSERT OR REPLACE INTO metadata VALUES('city', ?)", (city_name,))
        c.execute("INSERT OR REPLACE INTO metadata VALUES('nodes', ?)", (str(G.number_of_nodes()),))
        c.execute("INSERT OR REPLACE INTO metadata VALUES('edges', ?)", (str(G.number_of_edges()),))
        c.commit()
        log.info("Grafo salvato correttamente.")

    # ──────────────────────────────────────────
    # CARICAMENTO GRAFO
    # ──────────────────────────────────────────

    def load_graph(self) -> nx.DiGraph:
        """Ricostruisce il grafo NetworkX dal DB."""
        G = nx.DiGraph()

        nodes = self._conn.execute("SELECT id,lat,lon,name,amenity,highway FROM nodes").fetchall()
        for row in nodes:
            G.add_node(row[0], lat=row[1], lon=row[2],
                       name=row[3], amenity=row[4], highway=row[5])

        edges = self._conn.execute(
            "SELECT source,target,osmid,highway,name,length_m,weight,oneway,maxspeed FROM edges"
        ).fetchall()
        for row in edges:
            G.add_edge(row[0], row[1],
                       osmid=row[2], highway=row[3], name=row[4],
                       length_m=row[5], weight=row[6],
                       oneway=bool(row[7]), maxspeed=row[8])

        log.info("Grafo caricato dal DB: %d nodi, %d archi",
                 G.number_of_nodes(), G.number_of_edges())
        return G

    def graph_exists(self) -> bool:
        """True se il DB contiene già un grafo."""
        try:
            row = self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
            return row[0] > 0
        except Exception:
            return False

    # ──────────────────────────────────────────
    # POI
    # ──────────────────────────────────────────

    def save_pois(self, pois: List[Dict]) -> None:
        rows = [
            (float(p.get("lat", 0)), float(p.get("lon", 0)),
             p.get("display_name", p.get("name", "")),
             p.get("_amenity_type", ""),
             p.get("osm_id") or p.get("place_id"))
            for p in pois
        ]
        self._conn.execute("DELETE FROM pois")
        self._conn.executemany(
            "INSERT INTO pois(lat,lon,name,amenity_type,osm_id) VALUES(?,?,?,?,?)", rows
        )
        self._conn.commit()

    def load_pois(self) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT id,lat,lon,name,amenity_type FROM pois"
        ).fetchall()
        return [
            {"id": r[0], "lat": r[1], "lon": r[2], "name": r[3], "amenity_type": r[4]}
            for r in rows
        ]

    # ──────────────────────────────────────────
    # METADATA
    # ──────────────────────────────────────────

    def get_metadata(self) -> Dict[str, str]:
        rows = self._conn.execute("SELECT key,value FROM metadata").fetchall()
        return dict(rows)
