# ─────────────────────────────────────────────────────────────────────────────
#  Dockerfile — Image Python per la pipeline headless CityGraph
#  (fetch OSM → build graph → carica Neo4j)
#  Il renderer OpenGL/Pygame NON è incluso: gira sull'host con GPU.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# Metadati
LABEL maintainer="CityGraph Explorer"
LABEL description="Pipeline headless: OSM fetch → NetworkX → Neo4j"

# Evita prompt interattivi durante apt
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── Dipendenze di sistema ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Workdir ───────────────────────────────────────────────────────────────────
WORKDIR /app

# ── Dipendenze Python (solo quelle headless, senza moderngl/pygame) ───────────
COPY docker/requirements.headless.txt ./requirements.headless.txt
RUN pip install --no-cache-dir -r requirements.headless.txt

# ── Codice sorgente ───────────────────────────────────────────────────────────
COPY src/ ./src/

# ── Directory dati (cache OSM) ────────────────────────────────────────────────
RUN mkdir -p /app/data/cache

# ── Script di entrypoint ──────────────────────────────────────────────────────
COPY docker/scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
