# Makefile — Scorciatoie per gestire l'intera stack CityGraph
# Richiede: Docker Desktop, Python 3.12+, make (su Windows: tramite Git Bash o scoop install make)
#
# Comandi principali:
#   make up          → avvia Neo4j in background
#   make pipeline    → avvia Neo4j + pipeline Python (headless, nel container)
#   make run         → avvia l'app con renderer sull'HOST (dopo "make up")
#   make down        → ferma tutti i container
#   make logs        → tail log Neo4j
#   make browser     → apre Neo4j Browser nel browser di default
#   make status      → stato dei container
#   make clean       → rimuove container + volumi (ATTENZIONE: cancella il DB)

COMPOSE = docker compose
APP_CMD  = python src/main.py

.PHONY: up down pipeline run logs browser status clean reset-db \
        install shell-neo4j stats help

# ── DOCKER ─────────────────────────────────────────────────────────────────────

## Avvia solo Neo4j in background
up:
	$(COMPOSE) up -d neo4j
	@echo ""
	@echo "✓ Neo4j avviato"
	@echo "  Browser : http://localhost:7474"
	@echo "  Bolt    : bolt://localhost:7687"
	@echo "  User    : neo4j / Password: vedi .env"

## Avvia Neo4j + pipeline Python nel container (headless)
pipeline:
	$(COMPOSE) --profile app up --build
	@echo "✓ Pipeline completata — dati caricati in Neo4j"

## Ferma tutti i container (preserva i volumi/dati)
down:
	$(COMPOSE) down

## Ferma e rimuove TUTTI i volumi (cancella il DB Neo4j!)
clean:
	$(COMPOSE) down -v
	@echo "⚠  Volumi rimossi — Neo4j DB cancellato"

## Ricrea tutto da zero (down + up + pipeline)
reset-db: clean up
	@echo "Attendo che Neo4j sia pronto..."
	sleep 20
	$(COMPOSE) --profile app up --build

## Stato dei container
status:
	$(COMPOSE) ps

## Log Neo4j in streaming
logs:
	$(COMPOSE) logs -f neo4j

## Log pipeline app
logs-app:
	$(COMPOSE) logs -f app

## Shell interattiva nel container Neo4j
shell-neo4j:
	docker exec -it citygraph_neo4j bash

# ── APP HOST (renderer OpenGL) ─────────────────────────────────────────────────

## Installa dipendenze Python sull'host
install:
	pip install -r requirements.txt

## Avvia l'app completa sull'host (Neo4j deve girare: make up)
## Carica da Neo4j se i dati ci sono, altrimenti scarica da OSM
run:
	$(APP_CMD)

## Forza re-download dei dati OSM e ricarica Neo4j
run-force:
	$(APP_CMD) --force

## Cambia città (es: make city CITY="Milano, Italy")
city:
	$(APP_CMD) --city "$(CITY)"

## Statistiche grafo (senza renderer)
stats:
	$(APP_CMD) --stats

# ── UTILITY ────────────────────────────────────────────────────────────────────

## Apre Neo4j Browser (Windows)
browser:
	start http://localhost:7474

## Help
help:
	@echo ""
	@echo "CityGraph Explorer — Comandi disponibili"
	@echo "────────────────────────────────────────"
	@echo "  make up          Avvia Neo4j in background"
	@echo "  make pipeline    Avvia Neo4j + pipeline Python nel container"
	@echo "  make run         Avvia app completa sull'host (renderer OpenGL)"
	@echo "  make run-force   Re-download forzato OSM + reload Neo4j"
	@echo "  make city CITY=\"Milano, Italy\"   Carica una città specifica"
	@echo "  make stats       Statistiche grafo"
	@echo "  make down        Ferma i container"
	@echo "  make clean       Rimuove container + volumi (CANCELLA DB)"
	@echo "  make logs        Log Neo4j in streaming"
	@echo "  make browser     Apre Neo4j Browser nel browser"
	@echo "  make status      Stato container"
	@echo ""
