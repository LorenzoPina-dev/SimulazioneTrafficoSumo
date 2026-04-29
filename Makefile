# Makefile — CityGraph Explorer + SUMO Simulation
# Richiede: Docker Desktop, Python 3.12+, SUMO >= 1.20, make (Git Bash su Windows)
#
# Comandi principali:
#   make up          → avvia Neo4j in background
#   make pipeline    → Neo4j + pipeline Python headless (container)
#   make run         → viewer mappa ModernGL (sull'host, Neo4j deve girare)
#   make simulate    → simulazione SUMO interattiva (sull'host)
#   make down        → ferma i container
#   make clean       → rimuove tutto incl. volumi DB

COMPOSE  = docker compose
APP_CMD  = python src/main.py

.PHONY: up down pipeline run simulate run-force city stats \
        logs logs-app browser status clean shell-neo4j install help

# ── DOCKER ─────────────────────────────────────────────────────────────────────

## Avvia solo Neo4j in background
up:
	$(COMPOSE) up -d neo4j
	@echo ""
	@echo "✓ Neo4j avviato"
	@echo "  Browser : http://localhost:7474"
	@echo "  Bolt    : bolt://localhost:7687"

## Avvia Neo4j + pipeline Python headless nel container
pipeline:
	$(COMPOSE) --profile app up --build

## Ferma container (preserva volumi)
down:
	$(COMPOSE) down

## Ferma e rimuove TUTTO (cancella DB Neo4j!)
clean:
	$(COMPOSE) down -v
	@echo "⚠  Volumi rimossi — Neo4j DB cancellato"

## Stato container
status:
	$(COMPOSE) ps

## Log Neo4j
logs:
	$(COMPOSE) logs -f neo4j

## Log pipeline app
logs-app:
	$(COMPOSE) logs -f app

## Shell Neo4j
shell-neo4j:
	docker exec -it citygraph_neo4j bash

## Apre Neo4j Browser (Windows)
browser:
	start http://localhost:7474

# ── APP HOST ───────────────────────────────────────────────────────────────────

## Installa dipendenze Python
install:
	pip install -r requirements.txt

## Visualizzatore mappa ModernGL (Neo4j deve girare: make up)
run:
	$(APP_CMD)

## Simulazione SUMO interattiva (richiede SUMO_HOME + Neo4j)
simulate:
	$(APP_CMD) --simulate

## Re-download forzato OSM + reload Neo4j, poi viewer mappa
run-force:
	$(APP_CMD) --force

## Re-download forzato + simulazione SUMO
simulate-force:
	$(APP_CMD) --force --simulate

## Carica una città specifica nel viewer mappa
##   make city CITY="Milano, Italy"
city:
	$(APP_CMD) --city "$(CITY)"

## Carica una città specifica in SUMO
##   make simulate-city CITY="Bergamo, Italy"
simulate-city:
	$(APP_CMD) --city "$(CITY)" --simulate

## Statistiche grafo (senza renderer né simulazione)
stats:
	$(APP_CMD) --stats

# ── HELP ───────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "CityGraph Explorer + SUMO — Comandi"
	@echo "──────────────────────────────────────────────"
	@echo "  make up                    Avvia Neo4j in background"
	@echo "  make pipeline              Pipeline headless nel container"
	@echo "  make run                   Viewer mappa ModernGL"
	@echo "  make simulate              Simulazione SUMO interattiva"
	@echo "  make run-force             Re-download + viewer"
	@echo "  make simulate-force        Re-download + SUMO"
	@echo "  make city CITY='X, Y'      Viewer per città specifica"
	@echo "  make simulate-city CITY='X, Y'  SUMO per città specifica"
	@echo "  make stats                 Statistiche grafo"
	@echo "  make down                  Ferma container"
	@echo "  make clean                 Rimuove container + volumi DB (!)"
	@echo "  make logs                  Log Neo4j in streaming"
	@echo "  make browser               Apre Neo4j Browser"
	@echo ""
	@echo "  Prerequisiti:"
	@echo "    Docker Desktop   https://www.docker.com/products/docker-desktop"
	@echo "    SUMO >= 1.20     https://sumo.dlr.de/docs/Downloads.php"
	@echo "    SUMO_HOME        set SUMO_HOME=C:\\Program Files (x86)\\Eclipse\\Sumo"
	@echo ""
