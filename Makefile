### chorus — top-level make targets.
###
### The compose lifecycle (network/volumes/build/bundle/up/up-dev/dev/stop/down/
### logs/pre-commit/test) + the versioned image tag come from make/common.mk,
### vendored from nos-tromo/.github. Only chorus-specific config, the help
### text, and the app-specific targets (migrate/ingest/resolve/bootstrap)
### live here.
###
### Lifecycle ordering matters: data-plane must be healthy before the app
### comes up. `make bootstrap` runs the dependency check, then `make up`.

.DEFAULT_GOAL := help

REPO     := chorus
NETWORKS := inference-net data-net edge-net
VOLUMES  := chorus-state
include make/common.mk

.PHONY: help migrate ingest resolve bootstrap frontend-lint frontend-test

help:
	@echo "chorus — GraphRAG app (FastAPI backend + React SPA frontend)."
	@echo
	@echo "  make network    create the external inference-net + data-net + edge-net"
	@echo "  make volumes    create the external chorus-state Docker volume"
	@echo "  make build      build backend + frontend images"
	@echo "  make bundle     ship the built images as a versioned .tar.gz (latest annotated release tag)"
	@echo "  make bundle-dev like 'bundle', but from the current working tree (dev/soak)"
	@echo "  make up         start backend + frontend (detached, production shape, no host ports, no build)"
	@echo "  make up-dev     like 'up', but publishes backend + frontend ports on the host (no build)"
	@echo "  make dev        build backend + frontend, then up-dev"
	@echo "  make stop       stop containers (keep them)"
	@echo "  make down       stop + remove containers (never touches data-plane)"
	@echo "  make migrate    apply pending Neo4j migrations"
	@echo "  make ingest     run one ingestion pass from INGESTION_SOURCE_DIR"
	@echo "  make resolve    resolve aliases to canonical entities"
	@echo "  make bootstrap  wait for data-plane to be healthy, then up"
	@echo "  make pre-commit run pre-commit hooks (ruff + pyrefly)"
	@echo "  make verify     pre-push gate: pre-commit + frontend lint/build; mirrors CI's lint gate"
	@echo "  make test       run pytest"

# Apply pending Neo4j migrations against the configured NEO4J_URI.
migrate:
	$(COMPOSE) run --rm backend python -m chorus.migrations.cli apply

# Run one ingestion pass against the configured INGESTION_SOURCE_DIR.
ingest:
	$(COMPOSE_DEV) run --rm backend python -m chorus.ingestion.cli run

# Resolve unresolved :Alias nodes onto canonical :Entity nodes.
resolve:
	$(COMPOSE) run --rm backend python -m chorus.ingestion.cli resolve

# Wait for data-plane to be healthy, then bring the app up.
bootstrap: network volumes
	@./scripts/check_dataplane_health.sh
	$(MAKE) up

# Lint the React SPA (requires pnpm).
frontend-lint:
	cd frontend && pnpm install --frozen-lockfile && pnpm lint

# Run the React SPA unit tests (requires pnpm).
frontend-test:
	cd frontend && pnpm install --frozen-lockfile && pnpm test
