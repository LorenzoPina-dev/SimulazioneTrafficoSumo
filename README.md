# CityGraph Explorer

Visualizzatore GPU-accelerato di reti stradali OSM.  
**Stack:** Python 3.12 · ModernGL (OpenGL 3.3) · Pygame · NetworkX · Neo4j · Docker

---

## Struttura del progetto

```
testSumo/
├── .env                          ← credenziali e config (NON committare!)
├── .gitignore
├── docker-compose.yml            ← orchestrazione Neo4j + app headless
├── Makefile                      ← scorciatoie comandi
├── requirements.txt              ← dipendenze host (con renderer)
├── docker/
│   ├── Dockerfile                ← image Python headless (senza OpenGL)
│   ├── requirements.headless.txt ← dipendenze solo pipeline
│   ├── neo4j/
│   │   ├── conf/neo4j.conf       ← config Neo4j custom
│   │   └── plugins/              ← JAR plugin extra (APOC auto-scaricato)
│   └── scripts/
│       └── entrypoint.sh         ← wait-for-neo4j + avvio pipeline
├── data/
│   └── cache/                    ← cache JSON Overpass (condivisa host↔container)
└── src/
    ├── main.py                   ← entry point (host + headless)
    ├── config/settings.py        ← TUTTA la config (legge da env var)
    ├── fetchers/                 ← Overpass + Nominatim
    ├── graph/                    ← GraphBuilder + algoritmi
    ├── db/neo4j_manager.py       ← Neo4j CRUD (batch UNWIND)
    ├── renderer/                 ← GLRenderer (ModernGL) + Camera
    ├── ui/pipeline.py            ← orchestratore
    └── utils/                    ← logger, geo_utils
```

---

## Avvio rapido

### Prerequisiti
- **Docker Desktop** (Windows/Mac/Linux): https://www.docker.com/products/docker-desktop
- **Python 3.12+** (solo per il renderer sull'host)

### 1. Configura le credenziali

Il file `.env` è già incluso con valori di default funzionanti:

```env
NEO4J_PASSWORD=cityGraph2024!   # ← puoi cambiare questa
APP_CITY=Como, Italy             # ← città da caricare
```

### 2. Avvia Neo4j

```bash
# Con Makefile (Git Bash / Linux / Mac)
make up

# Oppure direttamente con Docker Compose
docker compose up -d neo4j
```

Neo4j sarà disponibile su:
- **Browser web**: http://localhost:7474 (user: `neo4j`, password: quella nel `.env`)
- **Bolt**: `bolt://localhost:7687`

### 3. Carica i dati (pipeline headless nel container)

```bash
# Con Makefile
make pipeline

# Oppure direttamente
docker compose --profile app up --build
```

Questo scarica la rete stradale da OpenStreetMap e la carica in Neo4j.  
La cache JSON viene salvata in `data/cache/` (condivisa tra host e container).

### 4. Avvia il renderer sull'host

```bash
# Installa dipendenze Python
pip install -r requirements.txt

# Avvia l'app (carica il grafo da Neo4j, apre la finestra OpenGL)
make run
# oppure:
python src/main.py
```

---

## Comandi utili

```bash
# ── Docker ──────────────────────────────────────────────────────
make up                          # avvia Neo4j
make pipeline                    # carica dati in Neo4j (container)
make down                        # ferma i container
make clean                       # rimuove tutto incl. volumi DB (!)
make logs                        # log Neo4j in streaming
make status                      # stato container
make browser                     # apre Neo4j Browser (Windows)

# ── App sull'host ────────────────────────────────────────────────
make run                         # renderer OpenGL + grafo da Neo4j
make run-force                   # re-download OSM + reload Neo4j
make city CITY="Milano, Italy"   # carica un'altra città
make stats                       # statistiche grafo, senza renderer

# ── Equivalenti senza make ───────────────────────────────────────
docker compose up -d neo4j
docker compose --profile app up --build
python src/main.py --city "Bergamo, Italy" --force
python src/main.py --stats
```

---

## Controlli renderer (finestra OpenGL)

| Azione | Input |
|---|---|
| Pan | WASD o Frecce |
| Zoom | Scroll mouse |
| Seleziona nodo partenza | Click sinistro (1°) |
| Calcola percorso → destinazione | Click sinistro (2°) |
| Reset vista | R |
| Cancella percorso | C |
| Esci | ESC |

---

## Configurazione (settings.py + .env)

Tutte le variabili leggono prima le env var (Docker) e poi i default.

| Env var | Default | Descrizione |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | URI Neo4j (auto: `bolt://neo4j:7687` nel container) |
| `NEO4J_PASSWORD` | `cityGraph2024!` | Password Neo4j |
| `NEO4J_BATCH_SIZE` | `2000` | Righe per transazione import |
| `APP_CITY` | `Como, Italy` | Città da caricare |
| `APP_FORCE_RELOAD` | `false` | `true` = re-scarica sempre |
| `APP_NO_RENDER` | `false` | `true` = headless (automatico nel container) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |

---

## Flusso dati

```
Overpass API ──► OverpassFetcher ──► JSON cache (data/cache/)
                        │
                  GraphBuilder ──► NetworkX DiGraph (in-memory)
                        │
                 Neo4jManager ──► Neo4j Docker
                   (UNWIND batch)    │
                                     ├─ shortestPath() Cypher
                                     └─ load_graph() → NetworkX
                                              │
                                       GLRenderer (ModernGL)
                                         VBO/VAO → GPU
```

---

## Perché Docker per Neo4j?

- **Zero installazione** manuale: `docker compose up` e funziona
- **Dati persistenti** nei volumi Docker (`citygraph_neo4j_data`)
- **Isolamento**: la versione Neo4j è fissata nell'immagine (`5.19-community`)
- **APOC plugin** pre-installato automaticamente
- **Portabilità**: stesso compose su Windows/Mac/Linux
