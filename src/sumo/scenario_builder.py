"""
scenario_builder.py — Genera i file .rou.xml e .sumocfg per libsumo.

Responsabilità:
  - Scrivere il file di rotte (.rou.xml) con i tipi di veicolo e,
    opzionalmente, route pre-definite
  - Scrivere il .sumocfg che lega net + route + parametri di simulazione
  - Gestire la pulizia dei file temporanei
"""

import os
import tempfile
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Tipi di veicolo pre-definiti
VEHICLE_TYPES = {
    "car": dict(
        vClass="passenger",
        length="4.5",
        accel="2.6",
        decel="4.5",
        sigma="0.5",
        maxSpeed="55.56",   # ≈ 200 km/h cap
        color="1,0,0",
    ),
    "truck": dict(
        vClass="truck",
        length="12.0",
        accel="1.0",
        decel="3.5",
        sigma="0.3",
        maxSpeed="33.33",   # 120 km/h
        color="0.8,0.4,0",
    ),
    "bus": dict(
        vClass="bus",
        length="12.0",
        accel="1.2",
        decel="4.0",
        sigma="0.2",
        maxSpeed="25.00",   # 90 km/h
        color="0,0,1",
    ),
    "motorcycle": dict(
        vClass="motorcycle",
        length="2.2",
        accel="4.0",
        decel="6.0",
        sigma="0.7",
        maxSpeed="55.56",
        color="0.8,0,0.8",
    ),
    "bicycle": dict(
        vClass="bicycle",
        length="1.8",
        accel="1.5",
        decel="3.0",
        sigma="0.4",
        maxSpeed="8.33",    # 30 km/h
        color="0,0.8,0",
    ),
}


class ScenarioBuilder:
    """
    Crea i file temporanei necessari per avviare libsumo.
    I file vengono creati in una directory temporanea e rimossi
    chiamando cleanup().
    """

    def __init__(self, net_path: str, sim_duration: int = 3600):
        self.net_path     = os.path.abspath(net_path)
        self.sim_duration = sim_duration
        self._rou_path: Optional[str] = None
        self._cfg_path: Optional[str] = None
        self._tmp_dir:  Optional[str] = None

    # ──────────────────────────────────────────
    # BUILD
    # ──────────────────────────────────────────

    def build(self) -> str:
        """
        Crea .rou.xml e .sumocfg in una directory temp.
        Ritorna il percorso assoluto del .sumocfg.
        """
        self._tmp_dir = tempfile.mkdtemp(prefix="citysumo_")
        self._rou_path = os.path.join(self._tmp_dir, "routes.rou.xml")
        self._cfg_path = os.path.join(self._tmp_dir, "sim.sumocfg")

        self._write_routes()
        self._write_cfg()

        log.info("Scenario creato in: %s", self._tmp_dir)
        return self._cfg_path

    def _write_routes(self) -> None:
        lines = ['<routes>']
        for vtype_id, attrs in VEHICLE_TYPES.items():
            attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
            lines.append(f'    <vType id="{vtype_id}" {attr_str}/>')
        lines.append('</routes>')
        with open(self._rou_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _write_cfg(self) -> None:
        cfg = f"""<configuration>
    <input>
        <net-file value="{self.net_path}"/>
        <route-files value="{self._rou_path}"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="{self.sim_duration}"/>
    </time>
    <processing>
        <ignore-route-errors value="true"/>
        <collision.action value="remove"/>
        <collision.check-junctions value="true"/>
        <lateral-resolution value="0.8"/>
    </processing>
    <report>
        <no-step-log value="true"/>
        <no-warnings value="true"/>
    </report>
</configuration>
"""
        with open(self._cfg_path, "w", encoding="utf-8") as f:
            f.write(cfg)

    # ──────────────────────────────────────────
    # CLEANUP
    # ──────────────────────────────────────────

    def cleanup(self) -> None:
        """Rimuove i file temporanei."""
        import shutil
        if self._tmp_dir and os.path.isdir(self._tmp_dir):
            try:
                shutil.rmtree(self._tmp_dir)
                log.debug("Temp dir rimossa: %s", self._tmp_dir)
            except Exception as exc:
                log.warning("Impossibile rimuovere temp dir: %s", exc)

    @property
    def cfg_path(self) -> Optional[str]:
        return self._cfg_path

    @property
    def rou_path(self) -> Optional[str]:
        return self._rou_path
