"""
net_exporter.py — Converte il grafo OSM in .net.xml SUMO.

Strategia:
  1. Usa netconvert (incluso in SUMO) se disponibile — produce un .net.xml
     completo con proiezioni, TLS e shape multi-punto corretti.
  2. Fallback al generatore interno se netconvert non è raggiungibile.

Fix principali nel generatore interno:
  - Le <connection> NON vengono generate per U-turn diretti (A→B poi B→A
    sulla stessa junction), che causano "invalid logic position" in SUMO.
  - Le <connection> non vengono generate quando from-edge e to-edge
    hanno la stessa coppia (from_node, to_node) invertita.
  - projParameter="!" → coordinate Cartesiane pure, nessuna chiamata PROJ.
"""

import json
import math
import logging
import os
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, Tuple, Optional, List

import networkx as nx

log = logging.getLogger(__name__)

EARTH_R = 6_378_137.0


def _lonlat_to_xy(lon: float, lat: float, lon0: float, lat0: float) -> Tuple[float, float]:
    x = EARTH_R * math.radians(lon - lon0) * math.cos(math.radians(lat0))
    y = EARTH_R * math.radians(lat - lat0)
    return x, y


def _point_to_segment(px, py, ax, ay, bx, by) -> Tuple[Tuple[float, float], float]:
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-10:
        return (ax, ay), math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return (cx, cy), math.hypot(px - cx, py - cy)


class SumoNetExporter:

    SPEED_MAP: Dict[str, float] = {
        "motorway": 130, "trunk": 110, "primary": 90, "secondary": 70,
        "tertiary": 50, "unclassified": 50, "residential": 30,
        "service": 20, "living_street": 10, "pedestrian": 5,
        "footway": 5, "cycleway": 15, "path": 5,
        "motorway_link": 80, "trunk_link": 60,
        "primary_link": 50, "secondary_link": 40, "tertiary_link": 40,
    }

    def __init__(self, G: nx.DiGraph, osm_cache_path: Optional[str] = None):
        if G.number_of_nodes() == 0:
            raise ValueError("Grafo vuoto.")
        self.G              = G
        self.osm_cache_path = osm_cache_path
        self.net_path: Optional[str] = None

        lats = [d["lat"] for _, d in G.nodes(data=True) if "lat" in d]
        lons = [d["lon"] for _, d in G.nodes(data=True) if "lon" in d]
        self.lat0 = sum(lats) / len(lats)
        self.lon0 = sum(lons) / len(lons)

        self._xy: Dict = {}
        self._net_junction_xy: Dict[str, Tuple[float, float]] = {}
        self._net_edges: List[Dict] = []
        self._net_bbox: Optional[Tuple[float, float, float, float]] = None
        self._compute_xy()

    def _compute_xy(self) -> None:
        for nid, data in self.G.nodes(data=True):
            if "lat" in data and "lon" in data:
                self._xy[nid] = _lonlat_to_xy(
                    data["lon"], data["lat"], self.lon0, self.lat0)
            else:
                self._xy[nid] = (0.0, 0.0)

    # ──────────────────────────────────────────
    # EXPORT PRINCIPALE
    # ──────────────────────────────────────────

    def export(self, output_path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        self.net_path = os.path.abspath(output_path)

        sumo_home  = os.environ.get("SUMO_HOME", "")
        netconvert = self._find_netconvert(sumo_home)
        type_file  = self._find_type_file(sumo_home)

        if netconvert:
            log.info("Uso netconvert per generare il .net.xml ...")
            osm_path = output_path.replace(".net.xml", ".osm.xml")
            self._write_osm_xml(osm_path)
            success = self._run_netconvert(netconvert, osm_path,
                                           self.net_path, type_file)
            if success:
                log.info("Net XML generato da netconvert: %s", self.net_path)
                self._load_net_geometry()
                return self.net_path
            log.warning("netconvert fallito — uso generatore interno")

        log.info("Generatore interno .net.xml ...")
        self._write_net_xml_internal(self.net_path)
        self._load_net_geometry()
        log.info("Net XML (interno) esportato: %s", self.net_path)
        return self.net_path

    # ──────────────────────────────────────────
    # RICERCA BINARI / FILE
    # ──────────────────────────────────────────

    @staticmethod
    def _find_netconvert(sumo_home: str) -> Optional[str]:
        candidates = []
        if sumo_home:
            for name in ("netconvert.exe", "netconvert"):
                candidates.append(os.path.join(sumo_home, "bin", name))
        candidates += ["netconvert.exe", "netconvert"]
        for c in candidates:
            if os.path.isfile(c):
                return c
            try:
                subprocess.run([c, "--version"], capture_output=True, timeout=5)
                return c
            except Exception:
                pass
        return None

    @staticmethod
    def _find_type_file(sumo_home: str) -> Optional[str]:
        """Cerca osmNetconvert.typ.xml nelle posizioni standard di SUMO."""
        if not sumo_home:
            return None
        print("SUMO_HOME:", sumo_home)
        candidates = [
            os.path.join(sumo_home, "data", "typemap", "osmNetconvert.typ.xml"),
            os.path.join(sumo_home, "bin",  "data", "typemap", "osmNetconvert.typ.xml"),
            os.path.join(sumo_home, "share", "sumo", "data", "typemap", "osmNetconvert.typ.xml"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                log.debug("Type file trovato: %s", c)
                return c
        log.warning(
            "osmNetconvert.typ.xml non trovato in SUMO_HOME=%s — "
            "netconvert userà i tipi di default", sumo_home)
        return None

    # ──────────────────────────────────────────
    # OSM XML (per netconvert)
    # ──────────────────────────────────────────

    def _write_osm_xml(self, osm_path: str) -> None:
        if self.osm_cache_path and os.path.isfile(self.osm_cache_path):
            log.info("Converto cache JSON Overpass → .osm: %s", self.osm_cache_path)
            with open(self.osm_cache_path, encoding="utf-8") as f:
                osm_data = json.load(f)
            self._json_to_osm_xml(osm_data, osm_path)
        else:
            log.info("Cache OSM non disponibile — ricostruisco .osm dal grafo")
            self._graph_to_osm_xml(osm_path)

    def _json_to_osm_xml(self, osm_data: dict, osm_path: str) -> None:
        root = ET.Element("osm", version="0.6")
        for el in osm_data.get("elements", []):
            t = el.get("type")
            if t == "node":
                n = ET.SubElement(root, "node",
                                  id=str(el["id"]),
                                  lat=str(el["lat"]),
                                  lon=str(el["lon"]),
                                  version="1")
                for k, v in el.get("tags", {}).items():
                    ET.SubElement(n, "tag", k=k, v=str(v))
            elif t == "way":
                w = ET.SubElement(root, "way", id=str(el["id"]), version="1")
                for ref in el.get("nodes", []):
                    ET.SubElement(w, "nd", ref=str(ref))
                for k, v in el.get("tags", {}).items():
                    ET.SubElement(w, "tag", k=k, v=str(v))
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(osm_path, encoding="utf-8", xml_declaration=True)

    def _graph_to_osm_xml(self, osm_path: str) -> None:
        root = ET.Element("osm", version="0.6")
        for nid, data in self.G.nodes(data=True):
            ET.SubElement(root, "node",
                          id=str(nid),
                          lat=str(data.get("lat", 0)),
                          lon=str(data.get("lon", 0)),
                          version="1")
        for wid, (u, v, data) in enumerate(self.G.edges(data=True), start=1):
            w = ET.SubElement(root, "way", id=str(wid), version="1")
            ET.SubElement(w, "nd", ref=str(u))
            ET.SubElement(w, "nd", ref=str(v))
            ET.SubElement(w, "tag", k="highway",
                          v=data.get("highway", "residential"))
            if data.get("oneway"):
                ET.SubElement(w, "tag", k="oneway", v="yes")
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(osm_path, encoding="utf-8", xml_declaration=True)

    # ──────────────────────────────────────────
    # NETCONVERT
    # ──────────────────────────────────────────

    def _run_netconvert(self, netconvert: str, osm_path: str,
                        net_path: str, type_file: Optional[str]) -> bool:
        cmd = [
            netconvert,
            "--osm-files",              osm_path,
            "--output-file",            net_path,
            "--geometry.remove",
            "--roundabouts.guess",
            "--junctions.join",
            "--no-internal-links",
            "--osm.sidewalks",          "false",
            "--osm.crossings",          "false",
            "--keep-edges.by-vclass",   "passenger,truck,bus,motorcycle,bicycle",
            "--remove-edges.by-vclass", "pedestrian",
            "--no-warnings",
        ]
        if type_file:
            cmd += ["--type-files", type_file]
        else:
            cmd += ["--proj.utm"]

        log.debug("netconvert cmd: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and os.path.isfile(net_path):
                return True
            log.warning("netconvert exit %d\nSTDOUT: %s\nSTDERR: %s",
                        result.returncode,
                        result.stdout[-2000:] if result.stdout else "",
                        result.stderr[-2000:] if result.stderr else "")
            return False
        except subprocess.TimeoutExpired:
            log.warning("netconvert timeout (>120s)")
            return False
        except Exception as exc:
            log.warning("netconvert exception: %s", exc)
            return False

    @staticmethod
    def _parse_shape_points(shape: str) -> List[Tuple[float, float]]:
        points: List[Tuple[float, float]] = []
        for token in (shape or "").split():
            try:
                x_str, y_str = token.split(",", 1)
                points.append((float(x_str), float(y_str)))
            except ValueError:
                continue
        return points

    def _load_net_geometry(self) -> None:
        try:
            tree = ET.parse(self.net_path)
            root = tree.getroot()

            self._net_junction_xy.clear()
            self._net_edges.clear()

            all_points: List[Tuple[float, float]] = []

            for j in root.findall("junction"):
                jid = j.get("id", "")
                x = float(j.get("x", 0))
                y = float(j.get("y", 0))
                self._net_junction_xy[jid] = (x, y)
                all_points.append((x, y))
                try:
                    nid = int(jid)
                    if nid in self._xy:
                        self._xy[nid] = (x, y)
                except ValueError:
                    pass

            for edge in root.findall("edge"):
                edge_id = edge.get("id", "")
                if not edge_id or edge_id.startswith(":"):
                    continue
                if edge.get("function"):
                    continue

                hw = edge.get("type", "") or ""
                if hw.startswith("highway."):
                    hw = hw.split(".", 1)[1]

                points = self._parse_shape_points(edge.get("shape", "") or "")
                if len(points) < 2:
                    for lane in edge.findall("lane"):
                        points = self._parse_shape_points(lane.get("shape", "") or "")
                        if len(points) >= 2:
                            break
                if len(points) < 2:
                    continue

                self._net_edges.append({
                    "id": edge_id,
                    "hw": hw,
                    "shape": points,
                })
                all_points.extend(points)

            if all_points:
                xs = [p[0] for p in all_points]
                ys = [p[1] for p in all_points]
                self._net_bbox = (min(xs), min(ys), max(xs), max(ys))
            else:
                self._net_bbox = None

            log.info("Geometria SUMO caricata: %d junction, %d edge",
                     len(self._net_junction_xy), len(self._net_edges))
        except Exception as exc:
            log.warning("_load_net_geometry fallito: %s", exc)
            self._net_junction_xy.clear()
            self._net_edges.clear()
            self._net_bbox = None

    # ──────────────────────────────────────────
    # GENERATORE INTERNO FALLBACK
    # ──────────────────────────────────────────

    def _write_net_xml_internal(self, net_path: str) -> None:
        """
        Genera un .net.xml minimale valido per SUMO.

        Regole critiche per evitare crash:
          1. projParameter="!"  → Cartesiano puro, nessuna chiamata PROJ.
          2. Le <connection> NON includono U-turn diretti: se l'edge in uscita
             dalla junction porta esattamente al nodo da cui arriva l'edge in
             entrata, la connection viene saltata.
             Motivo: SUMO calcola la geometria di svolta per ogni connection;
             un U-turn diretto (A→B poi B→A) produce una lunghezza logica
             negativa → "invalid logic position (0, max -1)" → SUMO si chiude.
          3. Ogni edge ha una lane esplicita con shape e length > 0.
        """
        from xml.dom import minidom

        root = ET.Element("net", version="1.20",
                          junctionCornerDetail="5",
                          limitTurnSpeed="5.50")

        xs = [xy[0] for xy in self._xy.values()]
        ys = [xy[1] for xy in self._xy.values()]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)

        ET.SubElement(root, "location",
                      netOffset="0.00,0.00",
                      convBoundary=f"{xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}",
                      origBoundary=f"{xmin-10:.2f},{ymin-10:.2f},{xmax+10:.2f},{ymax+10:.2f}",
                      projParameter="!")   # Cartesiano puro — nessun PROJ

        for hw, speed_kmh in self.SPEED_MAP.items():
            ET.SubElement(root, "type",
                          id=f"highway.{hw}",
                          priority="1", numLanes="1",
                          speed=f"{speed_kmh/3.6:.4f}",
                          oneway="0", width="3.2")

        # ── Edge + Lane ───────────────────────────────────────────────────────
        # edge_info[(u,v)] = edge_id  — usato dalla logica connection
        edge_set:  set = set()        # set di edge_id scritti
        edge_info: dict = {}          # (u,v) → edge_id

        for u, v, data in self.G.edges(data=True):
            if u not in self._xy or v not in self._xy:
                continue
            eid = f"e_{u}_{v}"
            if eid in edge_set:
                continue
            edge_set.add(eid)
            edge_info[(u, v)] = eid

            hw  = data.get("highway", "residential")
            if isinstance(hw, list):
                hw = hw[0] if hw else "residential"
            if hw not in self.SPEED_MAP:
                hw = "residential"

            spd = self.SPEED_MAP[hw] / 3.6
            ln  = max(1.0, float(data.get("length_m", 10) or 10))
            x1, y1 = self._xy[u]
            x2, y2 = self._xy[v]
            shp = f"{x1:.4f},{y1:.4f} {x2:.4f},{y2:.4f}"

            ee = ET.SubElement(root, "edge",
                               id=eid, **{"from": str(u), "to": str(v)},
                               type=f"highway.{hw}",
                               priority="1", numLanes="1",
                               speed=f"{spd:.4f}",
                               length=f"{ln:.4f}",
                               shape=shp,
                               spreadType="center")
            ET.SubElement(ee, "lane",
                          id=f"{eid}_0", index="0",
                          speed=f"{spd:.4f}",
                          length=f"{ln:.4f}",
                          shape=shp, width="3.2")

        # ── Junction ──────────────────────────────────────────────────────────
        for nid in self.G.nodes():
            x, y = self._xy.get(nid, (0.0, 0.0))
            inc = [f"e_{u}_{nid}" for u in self.G.predecessors(nid)
                   if f"e_{u}_{nid}" in edge_set]
            out = [f"e_{nid}_{v}" for v in self.G.successors(nid)
                   if f"e_{nid}_{v}" in edge_set]
            jt = "priority" if (inc or out) else "dead_end"
            ET.SubElement(root, "junction",
                          id=str(nid), type=jt,
                          x=f"{x:.4f}", y=f"{y:.4f}",
                          incLanes=" ".join(f"{e}_0" for e in inc),
                          intLanes="", shape="")

        # ── Connection ────────────────────────────────────────────────────────
        # Per ogni junction, collega ogni edge_in a ogni edge_out TRANNE:
        #   - edge_in == edge_out (stessa edge)
        #   - U-turn diretto: se edge_in = (A→nid) e edge_out = (nid→A),
        #     cioè l'uscita riporta esattamente al nodo di provenienza dell'entrata.
        #     Questo tipo di connection causa "invalid logic position" in SUMO.
        written_conns: set = set()

        for nid in self.G.nodes():
            # incoming edges: predecessori → nid
            in_edges = [
                (u, f"e_{u}_{nid}")
                for u in self.G.predecessors(nid)
                if f"e_{u}_{nid}" in edge_set
            ]
            # outgoing edges: nid → successori
            out_edges = [
                (v, f"e_{nid}_{v}")
                for v in self.G.successors(nid)
                if f"e_{nid}_{v}" in edge_set
            ]

            for (in_src, ie) in in_edges:
                for (out_dst, oe) in out_edges:
                    if ie == oe:
                        continue
                    # Salta U-turn diretto: A→nid poi nid→A
                    if out_dst == in_src:
                        continue
                    conn_key = (ie, oe)
                    if conn_key in written_conns:
                        continue
                    written_conns.add(conn_key)
                    ET.SubElement(root, "connection",
                                  **{"from": ie, "to": oe},
                                  fromLane="0", toLane="0",
                                  dir="s", state="M")

        n_conns = len(written_conns)
        log.info("Generatore interno: %d edge, %d connection scritte "
                 "(U-turn esclusi)", len(edge_set), n_conns)

        xml_str = minidom.parseString(
            ET.tostring(root, encoding="unicode")
        ).toprettyxml(indent="    ", encoding=None)
        lines = xml_str.split("\n")
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
        with open(net_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ──────────────────────────────────────────
    # UTILITY PUBBLICHE
    # ──────────────────────────────────────────

    def get_xy(self, osmid: int) -> Optional[Tuple[float, float]]:
        return self._xy.get(osmid)

    def get_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        return self._net_bbox

    def get_draw_edges(self) -> List[Dict]:
        return list(self._net_edges)

    def nearest_edge(self, x: float, y: float
                     ) -> Optional[Tuple[str, float, float]]:
        if self._net_edges:
            best_edge, best_dist, best_snap = None, float("inf"), (x, y)
            for edge in self._net_edges:
                pts = edge["shape"]
                for a, b in zip(pts, pts[1:]):
                    snap, dist = _point_to_segment(x, y, *a, *b)
                    if dist < best_dist:
                        best_dist = dist
                        best_edge = edge["id"]
                        best_snap = snap
            return (best_edge, *best_snap) if best_edge else None

        best_edge, best_dist, best_snap = None, float("inf"), (x, y)
        for u, v in self.G.edges():
            if u not in self._xy or v not in self._xy:
                continue
            snap, dist = _point_to_segment(x, y, *self._xy[u], *self._xy[v])
            if dist < best_dist:
                best_dist  = dist
                best_edge  = f"e_{u}_{v}"
                best_snap  = snap
        return (best_edge, *best_snap) if best_edge else None
