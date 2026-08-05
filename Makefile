# AIRA Gateway — developer task runner
# Run `make help` for available targets.

COMPOSE_DIR := deploy/compose
# Include the observability profile (OTel Collector + Grafana otel-lgtm) by default.
COMPOSE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml --profile observability
COMPOSE_CORE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml
# Infrastructure + the five application processes, all in containers.
COMPOSE_FULL := docker compose -f $(COMPOSE_DIR)/docker-compose.yml \
                -f $(COMPOSE_DIR)/docker-compose.apps.yml \
                --profile observability --profile demo
ENV_FILE := $(COMPOSE_DIR)/.env
ENV_EXAMPLE := $(COMPOSE_DIR)/.env.example

.DEFAULT_GOAL := help

.PHONY: help up up-core down destroy ps logs restart env sync test test-py test-frontend \
        test-integration test-e2e e2e lint lint-py lint-frontend fmt seed seed-reset \
        migrate-gateway kafka-topics relay consume run-gateway run-gateway-oidc run-backend \
        run-frontend up-full down-full logs-apps build-images ci wait-healthy prune

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

env: ## Create deploy/compose/.env from the example if missing
	@if [ ! -f "$(ENV_FILE)" ]; then \
		cp "$(ENV_EXAMPLE)" "$(ENV_FILE)"; \
		echo "Created $(ENV_FILE) from example."; \
	else \
		echo "$(ENV_FILE) already exists."; \
	fi

up: env ## Start the full stack (infra + observability)
	$(COMPOSE) up -d

up-core: env ## Start only core infra (no observability backend)
	$(COMPOSE_CORE) up -d

up-full: env ## Start EVERYTHING in containers (infra + gateway, management, consumer, relay, SPA)
	$(COMPOSE_FULL) up -d --build
	@echo "SPA: http://localhost:4200   (login ucadmin / demo-password)"

down-full: ## Stop the full containerised stack (keeps volumes)
	$(COMPOSE_FULL) down

logs-apps: ## Tail logs of the application containers only
	$(COMPOSE_FULL) logs -f --tail=100 gateway gateway-consumer management management-relay frontend

build-images: ## Build the three application images without starting anything
	$(COMPOSE_FULL) build gateway management frontend

down: ## Stop the stack (keep volumes)
	$(COMPOSE) down

destroy: ## Stop the stack and remove volumes (fresh state)
	$(COMPOSE) down -v

ps: ## Show service status and health
	$(COMPOSE) ps

logs: ## Tail logs of all services
	$(COMPOSE) logs -f --tail=100

restart: ## Restart the stack
	$(COMPOSE) restart

FRONTEND_DIR := management/frontend

sync: ## Install/refresh Python (uv) and frontend (npm) dependencies
	uv sync
	cd $(FRONTEND_DIR) && npm install

# What CI runs, and what to run before pushing. Deliberately the hermetic half: no Docker, no
# network, so it is fast and cannot fail for reasons unrelated to the change.
ci: lint test ## Run every hermetic gate (lint + types + unit tests with coverage) — what CI checks

test: test-py test-frontend ## Run all test suites (Python + frontend)

test-py: ## Run Python test suites with coverage gate
	uv run pytest

test-frontend: ## Run Angular unit tests (Vitest, single run) with the coverage gate
	cd $(FRONTEND_DIR) && npx ng test --watch=false

test-integration: ## Run server-side integration tests (needs the live stack; see tests/integration)
	uv run pytest -m integration --no-cov

wait-healthy: ## Block until the containerised stack answers (after up-full, and in CI)
	@echo "waiting for the stack to become ready…"
	@for i in $$(seq 1 80); do \
		if curl -sf http://127.0.0.1:4200/ >/dev/null 2>&1 \
			&& curl -sf http://127.0.0.1:8001/healthz >/dev/null 2>&1 \
			&& curl -sf http://127.0.0.1:8002/healthz >/dev/null 2>&1 \
			&& curl -sf http://127.0.0.1:8080/realms/aira/.well-known/openid-configuration >/dev/null 2>&1; \
		then echo "ready after $$((i * 3))s"; exit 0; fi; \
		sleep 3; \
	done; \
	echo "the stack did not become ready in time"; \
	$(COMPOSE_FULL) ps; exit 1

test-e2e: ## Run browser end-to-end tests (needs the stack + all services; see e2e/README.md)
	cd e2e && npm install --silent && npx playwright test

e2e: test-e2e

lint: lint-py lint-frontend ## Run all linters/type-checks (check mode)

lint-py: ## Run ruff lint + format check + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy gateway/src libs/src management/backend/src

lint-frontend: ## Check frontend formatting (Prettier) and types (build)
	cd $(FRONTEND_DIR) && npx prettier --check "src/**/*.{ts,html,scss}"
	cd $(FRONTEND_DIR) && npx ng build --configuration development

fmt: ## Auto-format and auto-fix the whole codebase
	uv run ruff format .
	uv run ruff check --fix .
	cd $(FRONTEND_DIR) && npx prettier --write "src/**/*.{ts,html,scss}"

# Local run targets enable OTLP export to the collector (make up starts it).
run-gateway: ## Run the Gateway API locally against the Compose stack
	AIRA_OTEL_ENABLED=true uv run uvicorn aira_gateway.main:app --reload --port 8001

# The SPA's dry-run and consumption views send their Keycloak bearer to the gateway, so it has
# to be able to verify it (ADR-0007).
run-gateway-oidc: ## Run the Gateway with OIDC enabled (required for the SPA's gateway views)
	AIRA_OTEL_ENABLED=true AIRA_OIDC_ENABLED=true \
		AIRA_OIDC_ISSUER=http://localhost:8080/realms/aira \
		uv run uvicorn aira_gateway.main:app --reload --port 8001

run-backend: ## Run the Management backend (Django) locally against the Compose stack
	cd management/backend && AIRA_OTEL_ENABLED=true uv run python manage.py runserver 127.0.0.1:8002

run-frontend: ## Run the Angular dev server (proxies /api to the management backend)
	cd $(FRONTEND_DIR) && npx ng serve --host 0.0.0.0 --port 4200 --proxy-config proxy.conf.json

migrate-gateway: ## Apply gateway DB migrations (Alembic)
	cd gateway && uv run alembic upgrade head

kafka-topics: ## Create the compacted config-distribution topics (idempotent)
	@for t in aira.usecases aira.memberships aira.api-keys aira.pipelines aira.budgets aira.rate-limits aira.models; do \
		docker exec aira-kafka /opt/kafka/bin/kafka-topics.sh --create --if-not-exists --topic $$t \
			--bootstrap-server localhost:9092 --partitions 1 --replication-factor 1 \
			--config cleanup.policy=compact; \
	done

prune: ## Apply the retention periods (removes stored payloads past their period)
	uv run python -m aira_gateway.retention

relay: ## Publish pending management outbox events to Kafka
	cd management/backend && uv run python manage.py relay

consume: ## Run the gateway config consumer (applies events into the read-model)
	uv run python -m aira_gateway.consumer.worker

seed: ## Migrate + seed demo data (idempotent; requires 'make up')
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py migrate --noinput
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo

seed-reset: ## Reset and reseed demo data
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo --fresh
