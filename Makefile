# AIRA Gateway — developer task runner
# Run `make help` for available targets.

COMPOSE_DIR := deploy/compose
COMPOSE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml
ENV_FILE := $(COMPOSE_DIR)/.env
ENV_EXAMPLE := $(COMPOSE_DIR)/.env.example

.DEFAULT_GOAL := help

.PHONY: help up down destroy ps logs restart env test lint fmt seed

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

up: env ## Start the local infrastructure stack
	$(COMPOSE) up -d

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

test: ## Run all test suites (populated per component in later slices)
	@echo "TODO: wire gateway/management/frontend test suites (FRD-000 slices 2-4)"

lint: ## Run linters/formatters in check mode (populated in later slices)
	@echo "TODO: wire ruff/black/mypy + eslint/prettier/tsc (FRD-000 slice 4)"

fmt: ## Auto-format the codebase (populated in later slices)
	@echo "TODO: wire ruff format / prettier (FRD-000 slice 4)"

seed: ## Seed demo data (implemented in FRD-002)
	@echo "TODO: implement demo seeding (FRD-002)"
