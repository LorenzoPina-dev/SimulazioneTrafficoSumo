"""
main.py — Punto di ingresso CityGraph Explorer.

Modalità:
  HOST  (con display) → fetch + Neo4j + renderer OpenGL
  DOCKER (headless)   → fetch + Neo4j, renderer saltato automaticamente
                        (rilevato da env APP_NO_RENDER=true)

Utilizzo:
  python main.py                          # città default da settings/env
  python main.py --city "Milano, Italy"
  python main.py --force                  # re-download anche se Neo4j ha dati
  python main.py --stats                  # statistiche grafo, niente renderer
  python main.py --no-render              # pipeline senza finestra grafica
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
    p = argparse.ArgumentParser(description="CityGraph Explorer — ModernGL + Neo4j")
    p.add_argument("--city",      "-c", type=str, default=None,
                   help="Città (es: 'Como, Italy'). Default da env APP_CITY o settings.py")
    p.add_argument("--country",         type=str, default=DEFAULT_CITY.country)
    p.add_argument("--force",     "-f", action="store_true",
                   help="Forza re-download anche se Neo4j ha già i dati")
    p.add_argument("--stats",           action="store_true",
                   help="Stampa statistiche grafo e termina")
    p.add_argument("--no-render",       action="store_true",
                   help="Pipeline headless: fetch+build+Neo4j senza renderer")
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

    # Rileva modalità headless da env (Docker) o da CLI
    headless = args.no_render or os.environ.get("APP_NO_RENDER", "").lower() in ("1", "true", "yes")
    force    = args.force     or os.environ.get("APP_FORCE",     "").lower() in ("1", "true", "yes")

    log.info("╔══════════════════════════════════════════════╗")
    log.info("║    CityGraph Explorer  [ModernGL + Neo4j]   ║")
    log.info("╚══════════════════════════════════════════════╝")
    log.info("Città    : %s", city.osm_area_query)
    log.info("Neo4j    : %s", __import__("config.settings", fromlist=["NEO4J"]).NEO4J.uri)
    log.info("Headless : %s", headless)

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

    # ── Rendering (solo host con display) ─────
    if not headless:
        pipeline.render()
    else:
        log.info("Modalità headless: renderer saltato.")
        log.info("Grafo disponibile in Neo4j — avvia l'app sull'host per visualizzarlo.")

    pipeline.db.close()
    log.info("Sessione terminata.")


if __name__ == "__main__":
    main()
