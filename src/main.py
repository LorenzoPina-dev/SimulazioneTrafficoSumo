"""
main.py — Punto di ingresso CityGraph Explorer + SUMO Simulation.

Modalità:
  HOST render   → fetch + Neo4j + renderer OpenGL (mappa interattiva)
  HOST simulate → fetch + Neo4j + SUMO (simulazione traffico interattiva)
  DOCKER        → fetch + Neo4j headless (APP_NO_RENDER=true)

Utilizzo:
  python main.py                           # visualizzatore mappa (default)
  python main.py --simulate                # simulazione SUMO interattiva
  python main.py --city "Milano, Italy"
  python main.py --force                   # re-download anche se Neo4j ha dati
  python main.py --stats                   # statistiche grafo, niente renderer
  python main.py --no-render               # pipeline headless
"""

import sys
import os
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logging
from config.settings import DEFAULT_CITY, CityConfig
from ui.pipeline import CityPipeline
from graph.algorithms import GraphAlgorithms

log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="CityGraph Explorer — ModernGL + Neo4j + SUMO",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--city",      "-c", type=str, default=None,
                   help="Città (es: 'Como, Italy'). Default da env APP_CITY o settings.py")
    p.add_argument("--country",         type=str, default=DEFAULT_CITY.country)
    p.add_argument("--force",     "-f", action="store_true",
                   help="Forza re-download anche se Neo4j ha già i dati")
    p.add_argument("--stats",           action="store_true",
                   help="Stampa statistiche grafo e termina")
    p.add_argument("--no-render",       action="store_true",
                   help="Pipeline headless: fetch+build+Neo4j senza renderer")
    p.add_argument("--simulate",  "-s", action="store_true",
                   help="Avvia il renderer SUMO interattivo invece del viewer mappa\n"
                        "(richiede SUMO_HOME impostato)")
    return p.parse_args()


def build_city_config(args: argparse.Namespace) -> CityConfig:
    if args.city:
        name = args.city.split(",")[0].strip()
        return CityConfig(name=name, country=args.country, osm_area_query=args.city)
    return DEFAULT_CITY


def main() -> None:
    setup_logging()
    args = parse_args()
    city = build_city_config(args)

    headless = args.no_render or os.environ.get("APP_NO_RENDER", "").lower() in ("1","true","yes")
    force    = args.force     or os.environ.get("APP_FORCE",     "").lower() in ("1","true","yes")
    simulate = args.simulate

    log.info("╔══════════════════════════════════════════════════╗")
    log.info("║  CityGraph Explorer  [ModernGL + Neo4j + SUMO]  ║")
    log.info("╚══════════════════════════════════════════════════╝")
    log.info("Città    : %s", city.osm_area_query)
    from config.settings import NEO4J
    log.info("Neo4j    : %s", NEO4J.uri)
    log.info("Modalità : %s", "SUMO simulation" if simulate else ("headless" if headless else "map viewer"))

    if simulate and "SUMO_HOME" not in os.environ:
        log.error("SUMO_HOME non impostato. Installa SUMO e imposta la variabile d'ambiente.")
        log.error("Esempio (Windows): set SUMO_HOME=C:\\Program Files (x86)\\Eclipse\\Sumo")
        sys.exit(1)

    pipeline = CityPipeline(city=city)

    # ── Carica o scarica ──────────────────────
    loaded = False
    if not force:
        loaded = pipeline.load_graph_from_db()

    if not loaded:
        pipeline.fetch_osm_data()
        pipeline.build_graph()
        pipeline.save_graph()
        pipeline.fetch_pois()

    # ── Statistiche ───────────────────────────
    if args.stats:
        stats = GraphAlgorithms.graph_stats(pipeline.G)
        print("\n─── Statistiche grafo ───────────────────────")
        for k, v in stats.items():
            print(f"  {k:<40}: {v}")
        print("────────────────────────────────────────────\n")
        pipeline.db.close()
        return

    # ── Rendering / Simulazione ───────────────
    if headless:
        log.info("Headless: renderer/simulatore saltato.")
        log.info("Grafo disponibile in Neo4j.")
    elif simulate:
        pipeline.simulate()
    else:
        pipeline.render()

    pipeline.db.close()
    log.info("Sessione terminata.")


if __name__ == "__main__":
    main()
