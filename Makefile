### chorus — top-level make targets.
###
### Lifecycle ordering matters: data-plane must be healthy before the app
### comes up. `make bootstrap` runs the dependency check, then `make up`.

.DEFAULT_GOAL := help

.PHONY: help network build bundle up stop down migrate ingest bootstrap pre-commit test

CHORUS_VERSION ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
export CHORUS_VERSION

COMPOSE := docker compose --env-file .env -f docker/compose.yaml -f docker/compose.override.yaml

help:
	@echo "chorus — GraphRAG app (FastAPI backend + Streamlit frontend)."
	@echo
	@echo "  make network    create the shared inference-net + data-net"
	@echo "  make build      build backend + frontend images"
	@echo "  make bundle     produce airgap artifacts (images tarball + wheelhouse)"
	@echo "  make up         start backend + frontend"
	@echo "  make stop       stop containers (keep them)"
	@echo "  make down       stop + remove containers (never touches data-plane)"
	@echo "  make migrate    apply pending Neo4j migrations"
	@echo "  make ingest     run one ingestion pass from INGESTION_SOURCE_DIR"
	@echo "  make bootstrap  wait for data-plane to be healthy, then up"
	@echo "  make pre-commit run pre-commit hooks (ruff + mypy)"
	@echo "  make test       run pytest"

# Create the shared external networks (one-time per host; idempotent).
network:
	docker network create inference-net >/dev/null 2>&1 || true
	docker network create data-net >/dev/null 2>&1 || true

# Build backend + frontend images.
build:
	DOCKER_BUILDKIT=1 $(COMPOSE) build

# Produce airgap delivery artifacts (images tarball + uv wheelhouse).
bundle:
	./scripts/build_wheelhouse.sh
	./scripts/bundle_images.sh

# Start backend + frontend (assumes data-plane reachable on inference-net).
up:
	$(COMPOSE) up -d

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
ingest:
	$(COMPOSE) run --rm backend python -m chorus.ingestion.cli run

# Wait for data-plane to be healthy, then bring the app up.
bootstrap: network
	@./scripts/check_dataplane_health.sh
	$(MAKE) up

# Run pre-commit hooks (ruff + mypy).
pre-commit:
	uv run pre-commit run --all-files

# Run the test suite.
test:
	uv run pytest -q
