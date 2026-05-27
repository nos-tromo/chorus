### chorus — top-level make targets.
###
### Lifecycle ordering matters: data-plane must be healthy before the app
### comes up. `make bootstrap` runs the dependency check, then `make up`.

.DEFAULT_GOAL := help

.PHONY: help network build bundle up up-dev stop down migrate bootstrap pre-commit test

CHORUS_VERSION ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
export CHORUS_VERSION

COMPOSE     := docker compose --env-file .env -f docker/compose.yaml
COMPOSE_DEV := docker compose --env-file .env -f docker/compose.yaml -f docker/compose.override.yaml

help:
	@echo "chorus — GraphRAG app (FastAPI backend + Streamlit frontend)."
	@echo
	@echo "  make network    create the shared inference-net + data-net"
	@echo "  make build      build backend + frontend images"
	@echo "  make bundle     produce airgap artifacts (images tarball + wheelhouse)"
	@echo "  make up         start backend + frontend (production shape, no host ports)"
	@echo "  make up-dev     like 'up', but publishes backend + frontend ports on the host"
	@echo "  make stop       stop containers (keep them)"
	@echo "  make down       stop + remove containers (never touches data-plane)"
	@echo "  make migrate    apply pending Neo4j migrations"
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
