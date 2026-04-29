"""
simulation_controller.py — Interfaccia Python ↔ SUMO tramite traci subprocess.

Fix rispetto alla versione precedente:
  - traci.start() viene chiamato SENZA port= e SENZA --remote-port nella cmd:
    traci.start() aggiunge --remote-port da solo quando passiamo port=.
    Passarlo anche noi causava "A value for the option 'remote-port' was already set."
  - Strategia corretta: trovare porta libera → passarla SOLO a traci.start(port=)
    → traci la comunica a sumo aggiungendo --remote-port internamente.
"""

import os
import sys
import socket
import random
import logging
import subprocess
from typing import Optional, List, Dict, Tuple

log = logging.getLogger(__name__)


def _ensure_sumo_tools() -> str:
    sumo_home = os.environ.get("SUMO_HOME", "")
    if not sumo_home:
        raise EnvironmentError(
            "SUMO_HOME non impostato.\n"
            "Windows: set SUMO_HOME=C:\\Program Files (x86)\\Eclipse\\Sumo"
        )
    tools = os.path.join(sumo_home, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return sumo_home


def _find_sumo_bin(sumo_home: str) -> str:
    for name in ("sumo.exe", "sumo"):
        full = os.path.join(sumo_home, "bin", name)
        if os.path.isfile(full):
            return full
    for name in ("sumo.exe", "sumo"):
        try:
            subprocess.run([name, "--version"], capture_output=True, timeout=3)
            return name
        except Exception:
            pass
    raise FileNotFoundError(
        f"Binario 'sumo' non trovato in {sumo_home}\\bin\\ né nel PATH."
    )


def _free_port() -> int:
    """Restituisce una porta TCP libera."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _try_libsumo() -> Optional[object]:
    if os.environ.get("SUMO_USE_TRACI", "").lower() in ("1", "true", "yes"):
        return None
    try:
        import libsumo as _ls
        _ = _ls.vehicle
        log.info("libsumo OK — uso headless embedded.")
        return _ls
    except Exception as exc:
        log.info("libsumo non disponibile (%s) — uso traci subprocess.", exc)
        return None


class SimulationController:

    def __init__(self, cfg_path: str, net_exporter,
                 default_vtype: str = "car"):
        self.cfg_path      = cfg_path
        self.net_exporter  = net_exporter
        self.default_vtype = default_vtype
        self._running       = False
        self._vehicle_count = 0
        self._route_count   = 0
        self._sim_time      = 0.0
        self._valid_edges:  List[str] = []
        self._traci         = None
        self._using_traci   = False

    # ──────────────────────────────────────────
    # START / STOP
    # ──────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        sumo_home = _ensure_sumo_tools()

        lib = _try_libsumo()
        if lib is not None:
            self._traci       = lib
            self._using_traci = False
            self._start_libsumo()
        else:
            import traci as _traci
            self._traci       = _traci
            self._using_traci = True
            self._start_traci(sumo_home)

        self._running  = True
        self._sim_time = 0.0
        self._cache_valid_edges()
        log.info("Simulazione avviata [%s] — edge validi: %d",
                 "traci" if self._using_traci else "libsumo",
                 len(self._valid_edges))

    def _start_libsumo(self) -> None:
        try:
            self._traci.start([
                "sumo", "-c", self.cfg_path,
                "--no-step-log", "--no-warnings",
            ])
        except Exception as exc:
            raise RuntimeError(f"libsumo.start() fallito: {exc}") from exc

    def _start_traci(self, sumo_home: str) -> None:
        sumo_bin = _find_sumo_bin(sumo_home)
        port     = _free_port()
        log.info("Avvio SUMO: %s (porta %d)", sumo_bin, port)
        log.info("Config: %s", self.cfg_path)

        # IMPORTANTE: passiamo SOLO -c alla cmd di sumo.
        # traci.start() aggiunge --remote-port=<port> da solo quando
        # gli passiamo port=. Se lo aggiungiamo anche noi → duplicato → crash.
        cmd = [
            sumo_bin,
            "-c",            self.cfg_path,
            "--no-step-log",
            "--no-warnings",
        ]

        try:
            # numRetries=20 e delay più alto per macchine lente
            self._traci.start(cmd, port=port, numRetries=20, label="main")
        except Exception as exc:
            raise RuntimeError(
                f"traci.start() fallito (porta {port}): {exc}\n"
                f"CMD: {' '.join(cmd)}"
            ) from exc

    def stop(self) -> None:
        if not self._running:
            return
        try:
            self._traci.close()
        except Exception:
            pass
        self._running = False
        log.info("Simulazione fermata.")

    def is_running(self) -> bool:
        return self._running

    # ──────────────────────────────────────────
    # STEP
    # ──────────────────────────────────────────

    def step(self) -> float:
        if not self._running:
            return self._sim_time
        try:
            self._traci.simulationStep()
            self._sim_time = self._traci.simulation.getTime()
        except Exception as exc:
            log.warning("Step error: %s", exc)
        return self._sim_time

    @property
    def time(self) -> float:
        return self._sim_time

    # ──────────────────────────────────────────
    # SPAWN
    # ──────────────────────────────────────────

    def spawn_at_xy(self, sumo_x: float, sumo_y: float,
                    vtype: Optional[str] = None) -> Optional[str]:
        if not self._running or not self._valid_edges:
            return None
        vt     = vtype or self.default_vtype
        result = self.net_exporter.nearest_edge(sumo_x, sumo_y)
        if result is None:
            return None
        edge_id, snap_x, snap_y = result
        route_id, route_edges = self._build_route_from_edge(edge_id, vt)
        if not route_edges:
            log.debug("Nessuna route trovata da edge %s per vtype=%s", edge_id, vt)
            return None

        self._vehicle_count += 1
        vid = f"veh_{self._vehicle_count}"
        try:
            self._traci.route.add(route_id, route_edges)
            self._traci.vehicle.add(vid, route_id, typeID=vt, depart="now")
            self._traci.vehicle.moveToXY(
                vid, edge_id, 0, snap_x, snap_y,
                angle=-1001.0, keepRoute=1,
            )
            log.debug("Spawn %s su %s route=%d edge", vid, edge_id, len(route_edges))
            return vid
        except Exception as exc:
            try:
                self._traci.vehicle.remove(vid)
            except Exception:
                pass
            log.debug("Spawn fallito: %s", exc)
            self._vehicle_count -= 1
            return None

    def spawn_random(self, count: int = 1,
                     vtype: Optional[str] = None) -> List[str]:
        if not self._running or not self._valid_edges:
            return []
        vt      = vtype or self.default_vtype
        created = []
        attempts = 0
        max_attempts = max(count * 12, 12)
        while len(created) < count and attempts < max_attempts:
            attempts += 1
            edge = random.choice(self._valid_edges)
            route_id, route_edges = self._build_route_from_edge(edge, vt)
            if not route_edges:
                continue

            self._vehicle_count += 1
            vid  = f"veh_{self._vehicle_count}"
            try:
                self._traci.route.add(route_id, route_edges)
                self._traci.vehicle.add(
                    vid, route_id, typeID=vt, depart="now",
                    departPos="random", departSpeed="max",
                )
                created.append(vid)
            except Exception as exc:
                log.debug("spawn_random su %s: %s", edge, exc)
                self._vehicle_count -= 1
        return created

    def _build_route_from_edge(self, start_edge: str, vtype: str,
                               min_edges: int = 2,
                               max_tries: int = 40) -> Tuple[str, List[str]]:
        if start_edge not in self._valid_edges:
            return "", []

        if len(self._valid_edges) <= 1:
            return "", []

        tries = min(max_tries, len(self._valid_edges) * 2)
        for _ in range(tries):
            dest_edge = random.choice(self._valid_edges)
            if dest_edge == start_edge:
                continue
            try:
                stage = self._traci.simulation.findRoute(start_edge, dest_edge, vType=vtype)
            except Exception as exc:
                log.debug("findRoute(%s -> %s, %s) fallita: %s",
                          start_edge, dest_edge, vtype, exc)
                continue

            edges = list(getattr(stage, "edges", ()) or ())
            if len(edges) < min_edges:
                continue
            if edges[0] != start_edge:
                continue

            self._route_count += 1
            route_id = f"route_{self._route_count}"
            return route_id, edges

        return "", []

    # ──────────────────────────────────────────
    # LETTURA STATO
    # ──────────────────────────────────────────

    def get_vehicle_positions(self) -> List[Dict]:
        if not self._running:
            return []
        out = []
        try:
            for vid in self._traci.vehicle.getIDList():
                try:
                    x, y  = self._traci.vehicle.getPosition(vid)
                    speed = self._traci.vehicle.getSpeed(vid)
                    vt    = self._traci.vehicle.getTypeID(vid)
                    angle = self._traci.vehicle.getAngle(vid)
                    length = self._traci.vehicle.getLength(vid)
                    width = self._traci.vehicle.getWidth(vid)
                    road_id = self._traci.vehicle.getRoadID(vid)
                    out.append({"id": vid, "x": x, "y": y,
                                "speed": speed, "type": vt,
                                "angle": angle, "length": length,
                                "width": width, "road_id": road_id})
                except Exception:
                    pass
        except Exception:
            pass
        return out

    def get_signal_states(self) -> List[Dict]:
        if not self._running:
            return []
        out: List[Dict] = []
        try:
            for tl_id in self._traci.trafficlight.getIDList():
                try:
                    state = self._traci.trafficlight.getRedYellowGreenState(tl_id)
                    lanes = self._traci.trafficlight.getControlledLanes(tl_id)
                    phase = self._traci.trafficlight.getPhase(tl_id)
                    next_switch = self._traci.trafficlight.getNextSwitch(tl_id)
                except Exception as exc:
                    log.debug("Lettura semaforo %s fallita: %s", tl_id, exc)
                    continue

                lane_states: Dict[str, str] = {}
                limit = min(len(state), len(lanes))
                for idx in range(limit):
                    lane_id = lanes[idx]
                    if not lane_id:
                        continue
                    lamp = state[idx]
                    current = lane_states.get(lane_id)
                    if current is None or self._signal_rank(lamp) > self._signal_rank(current):
                        lane_states[lane_id] = lamp

                out.append({
                    "id": tl_id,
                    "phase": phase,
                    "next_switch": next_switch,
                    "state": state,
                    "lane_states": lane_states,
                })
        except Exception:
            return []
        return out

    @staticmethod
    def _signal_rank(state_char: str) -> int:
        c = (state_char or "r")[0]
        if c in ("G", "g"):
            return 3
        if c in ("Y", "y", "u"):
            return 2
        return 1

    def get_stats(self) -> Dict:
        if not self._running:
            return {}
        try:
            return {
                "time":            self._sim_time,
                "vehicles_active": len(self._traci.vehicle.getIDList()),
                "vehicles_total":  self._vehicle_count,
                "teleports":       self._traci.simulation.getStartingTeleportNumber(),
                "collisions":      self._traci.simulation.getCollidingVehiclesNumber(),
            }
        except Exception:
            return {"time": self._sim_time}

    # ──────────────────────────────────────────
    # EDGE VALIDI
    # ──────────────────────────────────────────

    def _cache_valid_edges(self) -> None:
        try:
            valid = []
            for e in self._traci.edge.getIDList():
                if e.startswith(":"):
                    continue
                try:
                    if (self._traci.edge.getLaneNumber(e) > 0 and
                            self._traci.lane.getLength(f"{e}_0") > 5.0):
                        valid.append(e)
                except Exception:
                    continue
            self._valid_edges = valid
        except Exception as exc:
            log.warning("Cache edge fallita: %s", exc)
            self._valid_edges = []
