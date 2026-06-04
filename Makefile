### chorus — top-level make targets.
###
### Lifecycle ordering matters: data-plane must be healthy before the app
### comes up. `make bootstrap` runs the dependency check, then `make up`.

.DEFAULT_GOAL := help

.PHONY: help network volumes build bundle up up-dev stop down migrate ingest resolve bootstrap pre-commit test

# Versioned image tag.
# On production: read from .chorus-version written by bundle_images.sh.
# On dev: compute YYYY-MM-DD[-<short-sha>] on the fly.
# Override entirely by exporting CHORUS_VERSION before invoking make.
CHORUS_VERSION ?= $(shell \
    cat .chorus-version 2>/dev/null || \
    { _s=$$(git rev-parse --short HEAD 2>/dev/null); \
      echo "$$(date +%Y-%m-%d)$${_s:+-$$_s}"; } )
export CHORUS_VERSION

COMPOSE     := docker compose --env-file .env -f docker/compose.yaml
COMPOSE_DEV := docker compose --env-file .env -f docker/compose.yaml -f docker/compose.override.yaml

help:
	@echo "chorus — GraphRAG app (FastAPI backend + Streamlit frontend)."
	@echo
	@echo "  make network    create the shared inference-net + data-net"
	@echo "  make volumes    create the external chorus-state Docker volume"
	@echo "  make build      build backend + frontend images"
	@echo "  make bundle     produce airgap artifacts (images tarball + wheelhouse)"
	@echo "  make up         start backend + frontend (production shape, no host ports)"
	@echo "  make up-dev     like 'up', but publishes backend + frontend ports on the host"
	@echo "  make stop       stop containers (keep them)"
	@echo "  make down       stop + remove containers (never touches data-plane)"
	@echo "  make migrate    apply pending Neo4j migrations"
	@echo "  make ingest     run one ingestion pass from INGESTION_SOURCE_DIR"
	@echo "  make resolve    resolve aliases to canonical entities"
	@echo "  make bootstrap  wait for data-plane to be healthy, then up"
	@echo "  make pre-commit run pre-commit hooks (ruff + mypy)"
	@echo "  make test       run pytest"

# Create the shared external networks (one-time per host; idempotent).
network:
	docker network create inference-net >/dev/null 2>&1 || true
	docker network create data-net >/dev/null 2>&1 || true

# Create the external Docker volumes (one-time per host; idempotent).
# chorus-state holds audit log, raw store, and operational logs under
# CHORUS_HOME inside the container — survives `compose down -v`.
volumes:
	docker volume create chorus-state >/dev/null
	@echo "Ensured Docker volume exists: chorus-state"

# Build backend + frontend images.
build:
	DOCKER_BUILDKIT=1 $(COMPOSE) build

# Produce airgap delivery artifacts (images tarball + uv wheelhouse).
bundle:
	./scripts/build_wheelhouse.sh
	./scripts/bundle_images.sh

# Start backend + frontend in production shape (no host ports).
# Assumes data-plane is reachable on inference-net.
up:
	$(COMPOSE) up -d

# Like 'up' but layers compose.override.yaml on top to publish the
# backend (8000) and frontend (Streamlit) ports on the host.
up-dev:
	$(COMPOSE_DEV) up -d

# Stop containers without removing them.
stop:
	$(COMPOSE) stop

# Stop + remove containers (NEVER touches data-plane state).
down:
	$(COMPOSE) down

# Apply pending Neo4j migrations against the configured NEO4J_URI.
migrate:
	$(COMPOSE) run --rm backend python -m chorus.migrations.cli apply

# Run one ingestion pass against the configured INGESTION_SOURCE_DIR.
# Uses the dev compose so the host bind mount of ~/chorus/ingest is
# visible inside the container; without the override, the base
# compose.yaml only sees the chorus-state named volume which is empty
# in dev workflows.
ingest:
	$(COMPOSE_DEV) run --rm backend python -m chorus.ingestion.cli run

# Resolve unresolved :Alias nodes onto canonical :Entity nodes. Reads
# and writes Neo4j only (no source bind mount), so it uses the base
# compose like `migrate` — not COMPOSE_DEV.
resolve:
	$(COMPOSE) run --rm backend python -m chorus.ingestion.cli resolve

# Wait for data-plane to be healthy, then bring the app up.
bootstrap: network volumes
	@./scripts/check_dataplane_health.sh
	$(MAKE) up

# Run pre-commit hooks (ruff + mypy).
pre-commit:
	uv run pre-commit run --all-files

# Run the test suite.
test:
	uv run pytest -q
