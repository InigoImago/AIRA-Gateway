# AIRA Gateway — developer task runner
# Run `make help` for available targets.

COMPOSE_DIR := deploy/compose
# Include the observability profile (OTel Collector + Grafana otel-lgtm) by default.
COMPOSE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml --profile observability
COMPOSE_CORE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml
ENV_FILE := $(COMPOSE_DIR)/.env
ENV_EXAMPLE := $(COMPOSE_DIR)/.env.example

.DEFAULT_GOAL := help

.PHONY: help up up-core down destroy ps logs restart env sync test test-py test-frontend \
        lint lint-py lint-frontend fmt seed seed-reset migrate-gateway \
        run-gateway run-backend run-frontend

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

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

test: test-py test-frontend ## Run all test suites (Python + frontend)

test-py: ## Run Python test suites with coverage gate
	uv run pytest

test-frontend: ## Run Angular unit tests (Vitest, single run)
	cd $(FRONTEND_DIR) && npx ng test --watch=false

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

run-backend: ## Run the Management backend (Django) locally against the Compose stack
	cd management/backend && AIRA_OTEL_ENABLED=true uv run python manage.py runserver 127.0.0.1:8002

run-frontend: ## Run the Angular dev server
	cd $(FRONTEND_DIR) && npx ng serve --port 4200

migrate-gateway: ## Apply gateway DB migrations (Alembic)
	cd gateway && uv run alembic upgrade head

seed: ## Migrate + seed demo data (idempotent; requires 'make up')
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py migrate --noinput
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo

seed-reset: ## Reset and reseed demo data
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo --fresh
