#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  entrypoint.sh — Avvia la pipeline Python nel container.
#  Legge le variabili d'ambiente iniettate da docker-compose.
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo "════════════════════════════════════════════"
echo "  CityGraph Explorer — Pipeline container  "
echo "════════════════════════════════════════════"
echo "  Neo4j URI : ${NEO4J_URI:-bolt://neo4j:7687}"
echo "  Città     : ${APP_CITY:-Como, Italy}"
echo "  Force     : ${APP_FORCE:-false}"
echo "════════════════════════════════════════════"

# Attendi che Neo4j sia davvero pronto (oltre all'healthcheck di Docker)
echo "[entrypoint] Attendo Neo4j bolt su ${NEO4J_URI:-bolt://neo4j:7687}..."
python - <<'PYEOF'
import os, time, sys
from neo4j import GraphDatabase

uri  = os.environ.get("NEO4J_URI",      "bolt://neo4j:7687")
user = os.environ.get("NEO4J_USER",     "neo4j")
pwd  = os.environ.get("NEO4J_PASSWORD", "cityGraph2024!")

for attempt in range(1, 21):
    try:
        driver = GraphDatabase.driver(uri, auth=(user, pwd))
        driver.verify_connectivity()
        driver.close()
        print(f"[entrypoint] Neo4j pronto (tentativo {attempt})")
        sys.exit(0)
    except Exception as exc:
        print(f"[entrypoint] Tentativo {attempt}/20: {exc}")
        time.sleep(5)

print("[entrypoint] ERRORE: Neo4j non raggiungibile dopo 20 tentativi.")
sys.exit(1)
PYEOF

# Costruisci argomenti CLI per main.py
ARGS="--no-render"

if [ -n "${APP_CITY}" ]; then
    ARGS="${ARGS} --city \"${APP_CITY}\""
fi

if [ "${APP_FORCE:-false}" = "true" ]; then
    ARGS="${ARGS} --force"
fi

echo "[entrypoint] Avvio: python src/main.py ${ARGS}"
eval python src/main.py ${ARGS}
