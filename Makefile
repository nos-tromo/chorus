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

.PHONY: help migrate ingest resolve bootstrap frontend-lint

# Audited CLI commands run inside the running backend, so their audit chain
# lands in its log (AUDIT_OUTPUT=container) instead of a one-off container's,
# which can exit before the log collector sees it. The operator is the
# invoking host user; CASE_REF / REASON state the purpose, e.g.
#   make resolve CASE_REF=case-0001 REASON="monthly review"
AUDIT_EXEC = $(COMPOSE) exec \
	-e AUDIT_CLI_USER="$${SUDO_USER:-$$(id -un)}" \
	-e AUDIT_CASE_REF="$(CASE_REF)" -e AUDIT_REASON="$(REASON)" \
	backend

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
	@echo "  make logs       follow the compose logs (last 100 lines first)"
	@echo "  make migrate    apply pending Neo4j migrations (in the running backend)"
	@echo "  make ingest     run one ingestion pass from INGESTION_SOURCE_DIR (in the running backend)"
	@echo "  make resolve    resolve aliases to canonical entities (in the running backend)"
	@echo "                  migrate/ingest/resolve are audited: the backend must be up;"
	@echo "                  CASE_REF=... REASON=... state the purpose"
	@echo "  make bootstrap  wait for data-plane to be healthy, then up"
	@echo "  make pre-commit run pre-commit hooks (ruff + pyrefly)"
	@echo "  make verify     pre-push gate: pre-commit + frontend lint/build; mirrors CI's lint gate"
	@echo "  make frontend-lint  eslint the React SPA alone (the SPA half of 'verify')"
	@echo "  make test       run pytest + vitest (test-backend / test-frontend for one)"

# Apply pending Neo4j migrations against the configured NEO4J_URI.
migrate:
	$(AUDIT_EXEC) python -m chorus.migrations.cli apply

# Run one ingestion pass against INGESTION_SOURCE_DIR as the running backend
# sees it: the host drop dir (INGEST_HOST_DIR, default ~/chorus/ingest) when
# the backend was started with the dev overlay (make dev / make up-dev),
# CHORUS_HOME/ingest on the chorus-state volume in the production shape.
ingest:
	$(AUDIT_EXEC) python -m chorus.ingestion.cli run

# Resolve unresolved :Alias nodes onto canonical :Entity nodes.
resolve:
	$(AUDIT_EXEC) python -m chorus.ingestion.cli resolve

# Wait for data-plane to be healthy, then bring the app up.
bootstrap: network volumes
	@./scripts/check_dataplane_health.sh
	$(MAKE) up

# Lint the React SPA (requires pnpm).
frontend-lint:
	cd frontend && pnpm install --frozen-lockfile && pnpm lint
