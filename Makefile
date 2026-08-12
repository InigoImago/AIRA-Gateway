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

# The two models `make verify-up` pulls (FRD-123). Small on purpose: what is under test is the
# gateway, not the answer, and the size is what keeps a CI job from pulling gigabytes.
LOCAL_CHAT_MODEL ?= qwen3:0.6b
LOCAL_EMBED_MODEL ?= all-minilm

.DEFAULT_GOAL := help

.PHONY: help up up-core down destroy ps logs restart env sync test test-py test-frontend \
        test-integration test-e2e e2e lint lint-py lint-frontend fmt seed seed-reset \
        migrate-gateway kafka-topics relay consume run-gateway run-gateway-oidc run-backend \
        verify-up verify-down test-verify \
        run-frontend up-full down-full logs-apps build-images ci wait-healthy prune mutants

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

showcase: env ## Start the full demo: stack, local model, seeded roles/budgets, and real traffic
	@echo "==> starting the stack (this pulls a model on the first run and takes a few minutes)"
	$(COMPOSE_FULL) --profile demo up -d --build
	@# `wait-healthy`, not a second loop of its own. This waited on the **gateway** alone and then
	@# printed "SPA http://localhost:4200" — so on a machine where the frontend took a few seconds
	@# longer, the one URL the walkthrough starts at answered nothing. Two ideas of "ready", and the
	@# weaker one was the one this target used.
	@$(MAKE) --no-print-directory wait-healthy
	@# The seed deliberately waits only for the pull to **start** (`docker-compose.apps.yml` says
	@# why: a blocked registry must not cost the accounts, use cases and budgets that have nothing
	@# to do with models). Its companion rule is that it catalogues only models the endpoint really
	@# serves. On a **first** run those two combine into a demo with no models in it — correct by
	@# both rules and not a demo — so the pull is waited for here and the seed, which is
	@# idempotent, runs once more against what the pull actually produced.
	@echo "==> waiting for the model pull to finish"
	@for i in $$(seq 1 300); do \
		s=$$(docker inspect -f '{{.State.Status}}' aira-ollama-pull 2>/dev/null || echo gone); \
		[ "$$s" = "running" ] || break; \
		sleep 3; \
	done
	@echo "==> seeding again, now that the models are on disk"
	@$(COMPOSE_FULL) --profile demo run --rm --no-deps management-seed >/dev/null
	@# **Not a sleep.** This waited six seconds "for the read model to catch up", which is not a
	@# statement about anything. `demo_wait_ready.py` asks the question the traffic is about to
	@# ask: will the gateway accept a demo credential and serve the demo's model? That is true
	@# only when the pull, the seed, the relay, Kafka and the consumer have all done their work.
	@echo "==> waiting until the gateway accepts the demo's credentials and model"
	uv run python tools/demo_wait_ready.py
	@echo "==> clearing what earlier runs consumed, so this run tells its own story"
	@-uv run python tools/demo_reset_usage.py
	@echo "==> driving real traffic so the reports are not empty"
	@# No leading `-`: the traffic decides whether this showcase is worth showing. It swallowed a
	@# run in which all ten requests were refused and the target still reported success, then
	@# printed the login table over a demo with nothing in it.
	uv run python tools/demo_traffic.py
	@echo ""
	@echo "  Start here:"
	@echo "    Console     http://localhost:4200   the SPA — everything below is reached from it"
	@echo "    Keycloak    http://localhost:8080/admin/master/console/#/aira/users   admin / admin"
	@# Read from the running realm, not asserted. Somebody opened the admin console, saw one user
	@# called `admin` and no groups, and concluded the seed had failed — it had not: that console
	@# signs you in to the *master* realm and the demo lives in `aira`. Two accounts named `admin`
	@# in two realms, and this block used to name neither.
	@-KEYCLOAK_URL=http://localhost:$${AIRA_KEYCLOAK_PORT:-8080} \
		KEYCLOAK_REALM_FILE=deploy/compose/keycloak/realms/aira-realm.json \
		python3 tools/keycloak_demo_realm.py --report
	@echo ""
	@echo "  Serving, with no user interface of their own:"
	@echo "    Gateway     http://localhost:8001   the API that models are called through"
	@echo "    Management  http://localhost:8002   the control-plane API (/api/v1/...)"
	@echo ""
	@echo "  Log in **to the console** as any of these — password 'demo-password'. These are"
	@echo "  accounts in the 'aira' realm, and are not the Keycloak admin above:"
	@echo ""
	@echo "    admin      global administrator   every use case, and the only role that may price a model"
	@echo "    itgov      IT Steuerung           every use case and the whole spend report, read-only"
	@echo "    itsec      IT Security            the governance view"
	@echo "    ucadmin    use-case administrator three of the four — 'personalwesen' is invisible"
	@echo "    ucuser     use-case user          in 'kundenservice' and 'coding-assistant', read-only"
	@echo ""
	@echo "==> a coding assistant, governed end to end"
	@uv run python tools/showcase_agent.py
	@echo ""

# Deliberately no reset here: this is the target for watching the bars fill and a limit be
# reached, which is the thing `showcase` resets so that *its* run is the one you see.
showcase-traffic: ## Drive more demo traffic (moves the budget bars)
	uv run python tools/demo_traffic.py

showcase-reset-keys: ## Let the demo's API keys be reissued after their use case was deleted
	@echo "Removing the demo keys from the gateway's read-model so the seed can announce them again."
	@echo "Demo only. Revocation is terminal in the product, and deliberately so: no event may"
	@echo "resurrect a revoked credential. This deletes the rows instead, for four known slugs."
	$(COMPOSE_CORE) exec -T postgres psql -U aira -d aira_gateway -c \
		"delete from api_keys where use_case in ('kundenservice','entwicklung','personalwesen','coding-assistant')"
	$(COMPOSE_FULL) run --rm management-seed
	@echo "waiting for the announcements to reach the gateway…"
	@# Polled, not slept. A fixed wait is a guess about the relay's poll interval plus the
	@# consumer's, and when the guess is short the command reports the state it just created as
	@# broken — which is exactly the confusion this whole target exists to end.
	@for i in $$(seq 1 40); do \
		n=$$($(COMPOSE_CORE) exec -T postgres psql -U aira -d aira_gateway -tAc \
			"select count(*) from api_keys where use_case in ('kundenservice','entwicklung','personalwesen','coding-assistant')" 2>/dev/null | tr -d ' '); \
		[ "$$n" = "4" ] && break; \
		sleep 2; \
	done
	@python3 tools/showcase_doctor.py

showcase-doctor: ## Report which link of the demo is broken (reads only, changes nothing)
	@python3 tools/showcase_doctor.py

showcase-agent: ## Write an OpenCode config for the showcase's coding-assistant use case
	@uv run python tools/showcase_agent.py

down-full: ## Stop the full containerised stack (keeps volumes)
	$(COMPOSE_FULL) down

logs-apps: ## Tail logs of the application containers only
	$(COMPOSE_FULL) logs -f --tail=100 gateway gateway-consumer management management-relay frontend

build-images: ## Build the three application images without starting anything
	$(COMPOSE_FULL) build gateway management frontend

verify-up: env ## Start a real local model (FRD-123) and pull the two verification models
	$(COMPOSE) --profile verify up -d ollama
	@echo "waiting for the endpoint..."
	@until curl -fsS http://localhost:11434/api/version >/dev/null 2>&1; do sleep 1; done
	@echo "pulling models (hundreds of MB, once per machine)..."
	docker exec aira-ollama ollama pull $(LOCAL_CHAT_MODEL)
	docker exec aira-ollama ollama pull $(LOCAL_EMBED_MODEL)
	@echo
	@echo "Point the gateway at it:"
	@echo "  AIRA_OLLAMA_URL=http://localhost:11434 \\"
	@echo "  AIRA_OLLAMA_MODELS=$(LOCAL_CHAT_MODEL) \\"
	@echo "  AIRA_OLLAMA_EMBEDDING_MODELS=$(LOCAL_EMBED_MODEL) make run-gateway-oidc"

verify-down: ## Stop the local model (keeps the downloaded weights)
	$(COMPOSE) --profile verify stop ollama

test-verify: ## Integration tests that need a real local model (skips cleanly without one)
	uv run pytest -m integration --no-cov tests/integration/test_local_model.py

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

mutants: ## Break each guarded property on purpose and check the tests notice (see tools/mutation_check.py)
	uv run python tools/mutation_check.py

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
	@for t in aira.usecases aira.memberships aira.api-keys aira.pipelines aira.budgets aira.rate-limits aira.models aira.anomaly-rules; do \
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

vault-init: ## Create the Vault path, policy and AppRole; optionally copy AIRA_* secrets in
	@echo "Setting up Vault at $${VAULT_ADDR:-http://localhost:8200}…"
	uv run python tools/vault_setup.py --from-env

vault-status: ## Where does the gateway say its secrets came from?
	@curl -s http://localhost:8001/readyz | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('secrets', {'note': 'authenticate to see this'}), indent=2))"

seed: ## Migrate + seed demo data (idempotent; requires 'make up')
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py migrate --noinput
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo

seed-reset: ## Reset and reseed demo data
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo --fresh
