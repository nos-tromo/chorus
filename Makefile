### chorus — top-level make targets.
###
### Lifecycle ordering matters: data-plane must be healthy before the app
### comes up. `make bootstrap` runs the dependency check, then `make up`.

.PHONY: help network build up stop down bundle migrate bootstrap fmt lint type test

CHORUS_VERSION ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
export CHORUS_VERSION

COMPOSE := docker compose -f docker/compose.yaml -f docker/compose.override.yaml

help: ## list targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

network: ## create the shared inference-net (idempotent)
	docker network create inference-net >/dev/null 2>&1 || true

build: ## build backend + frontend images
	DOCKER_BUILDKIT=1 $(COMPOSE) build

up: ## start backend + frontend (assumes data-plane reachable on inference-net)
	$(COMPOSE) up -d

stop: ## stop containers without removing them
	$(COMPOSE) stop

down: ## stop + remove containers (NEVER touches data-plane state)
	$(COMPOSE) down

migrate: ## apply pending Neo4j migrations against the configured NEO4J_URI
	$(COMPOSE) run --rm api python -m chorus.migrations.cli apply

bootstrap: network ## wait for data-plane, then bring app up
	@./scripts/check_dataplane_health.sh
	$(MAKE) up

bundle: ## produce airgap delivery artifacts (images tarball + wheelhouse)
	./scripts/build_wheelhouse.sh
	./scripts/bundle_images.sh

fmt: ## format
	uv run ruff format .

lint: ## lint
	uv run ruff check .

type: ## type check
	uv run mypy .

test: ## run pytest
	uv run pytest -q
