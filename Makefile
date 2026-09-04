# AIRA Gateway — developer task runner
# Run `make help` for available targets.

COMPOSE_DIR := deploy/compose
# Include the observability profile (OTel Collector + Grafana otel-lgtm) by default.
COMPOSE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml --profile observability
COMPOSE_CORE := docker compose -f $(COMPOSE_DIR)/docker-compose.yml

# The three files, and which combination means what. `tools/compose_files.py` is the same list for
# Python; `tools/tests/test_one_owner_for_the_stack_addresses.py` fails on a stray literal.
INFRA_F    := -f $(COMPOSE_DIR)/docker-compose.yml
APPS_F     := -f $(COMPOSE_DIR)/docker-compose.apps.yml
SHOWCASE_F := -f $(COMPOSE_DIR)/docker-compose.showcase.yml

# Infrastructure + the application processes: **what a real deployment runs**, and nothing that
# exists for the demo. Startable on its own — `tools/tests/test_the_core_stack_carries_no_demo.py`
# fails if a demo service or a demo dependency creeps back into it.
COMPOSE_APPS := docker compose $(INFRA_F) $(APPS_F) --profile observability

# The above, plus the demo provisioning: a development Keycloak realm, a `-dev` Vault that forgets
# on every restart, and the seeded accounts.
COMPOSE_FULL := docker compose $(INFRA_F) $(APPS_F) $(SHOWCASE_F) \
                --profile observability --profile demo

# Every service this repository can start — both files, every profile.
#
# **Stopping is not the mirror of starting, and that is the whole point.** A `up` target may
# legitimately start a subset; a `down` target has to deal with whatever is *there*, which is the
# union of everything any `up` target could have left behind. Those were two different sets, and
# `down` had the smaller one: it named `docker-compose.yml` and the observability profile, so after
# `make showcase` it knew 8 of the 21 services that were running.
#
# Measured on 2026-08-13, on a machine somebody had run the showcase on: `make down` removed the
# infrastructure and left `gateway`, `management`, `gateway-consumer`, `management-relay`,
# `frontend` and `gateway-retention` up — for eight hours, with the consumer crash-looping against
# the Postgres that had just been deleted out from under it. It could not even remove its own
# network (`Network aira Resource is still in use`) and **exited 0 while saying so**.
#
# Both halves are needed and neither substitutes for the other. `--remove-orphans` deals with a
# container whose service is not in the model at all — a renamed or deleted service — and it does
# **not** touch one that is in the model behind an inactive profile: tested, and `ollama` survived
# it. The profiles are what reach those.
#
# The profiles are listed rather than passed as `--profile "*"`. The wildcard is self-maintaining
# and needs Compose v2.24; on anything older it matches a profile literally called `*`, which is
# silently this same bug again. A written list has a counterpart instead —
# `tools/tests/test_compose_lifecycle_covers_the_stack.py` fails when a profile is declared that
# this line does not name.
COMPOSE_ALL := docker compose $(INFRA_F) $(APPS_F) $(SHOWCASE_F) \
               --profile observability --profile demo --profile verify --profile debug
ENV_FILE := $(COMPOSE_DIR)/.env
ENV_EXAMPLE := $(COMPOSE_DIR)/.env.example

# The two models `make verify-up` pulls (FRD-123). Small on purpose: what is under test is the
# gateway, not the answer, and the size is what keeps a CI job from pulling gigabytes.
LOCAL_CHAT_MODEL ?= qwen3:0.6b
LOCAL_EMBED_MODEL ?= all-minilm

# ---- where the stack answers -------------------------------------------------------------------
#
# **Never write a port in this file.** Every address below is derived from the same
# `AIRA_PUBLISH_…` variable that publishes it in Compose, through `tools/stack_addresses.py`, which
# reads the environment, then `deploy/compose/.env`, then the default written in the Compose file
# itself — Compose's own order, so a value never means two things depending on who read it.
#
# The published ports became variables on 2026-08-18, after a parallel system collided with them.
# This file did not notice: it carried twenty literal addresses, so moving a port brought the stack
# up correctly and left `make showcase` waiting forever on the old one, with an error naming
# neither the port nor the variable. `tools/tests/test_one_owner_for_the_stack_addresses.py` fails
# when a literal reappears here or anywhere else that talks to the stack.
#
# **One process, `python3`, at parse time.** This runs on every `make` invocation including
# `make help`; fourteen `uv run` calls would put four seconds in front of every target, and a
# developer who pays that on `make help` starts working around the Makefile. The module imports
# nothing outside the standard library so that no dependency resolver sits on this path.
STACK_ADDRESSES := $(shell python3 tools/stack_addresses.py make)
stack_url = $(patsubst $(1)=%,%,$(filter $(1)=%,$(STACK_ADDRESSES)))

GATEWAY_URL    := $(call stack_url,gateway)
MANAGEMENT_URL := $(call stack_url,management)
CONSOLE_URL    := $(call stack_url,console)
KEYCLOAK_URL   := $(call stack_url,keycloak)
OLLAMA_URL     := $(call stack_url,ollama)
VAULT_URL      := $(call stack_url,vault)
GRAFANA_URL    := $(call stack_url,grafana)
INSPECTOR_URL  := $(call stack_url,otlp_inspector)
# The bare port, for the sentence that tells somebody on **another machine** where to point:
# the container name and its 4318 exist only inside this stack's network.
INSPECTOR_PORT := $(lastword $(subst :, ,$(INSPECTOR_URL)))
KAFKA_ADDR     := $(call stack_url,kafka.netloc)

# The bare ports, for the two dev targets that **bind** rather than connect: `ng serve` and Django's
# `runserver`. They take the published port on purpose — those processes stand in for the
# containerised ones, so a port moved to dodge a collision has to move for both or the dev stack
# collides with exactly the thing the variable was introduced to avoid.
CONSOLE_PORT    := $(lastword $(subst :, ,$(CONSOLE_URL)))
MANAGEMENT_PORT := $(lastword $(subst :, ,$(MANAGEMENT_URL)))

.DEFAULT_GOAL := help

.PHONY: help up up-core down destroy ps logs restart env sync test test-py test-frontend \
        test-integration test-e2e e2e lint lint-py lint-frontend fmt seed seed-reset \
        migrate-gateway kafka-topics relay consume run-gateway run-gateway-oidc run-backend \
        purge-e2e-use-cases config-verify config-check up-apps otel-status otel-arrivals \
        otlp-inspector otlp-inspector-down \
        verify-up verify-down test-verify \
        run-frontend up-full down-full logs-apps build-images ci wait-healthy prune mutants

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

env: ## Create deploy/compose/.env from the example if missing
	@# **And make the payload directory writable by the collector**, which runs as uid 10001 while
	@# this checkout belongs to whoever cloned it. Without this, `AIRA_OTEL_ARRIVED_FILE` pointing
	@# into it produces *nothing at all* — and the collector counts every batch as **sent**, so
	@# `make otel-status` reports delivery for a file that was never created. Measured on
	@# 2026-09-02: `otelcol_exporter_sent_spans{exporter="file/arrived"} 116`, and no file.
	@#
	@# Git cannot carry a directory mode, so it is set here rather than committed — and here
	@# because every `up*` target depends on `env`.
	@mkdir -p $(COMPOSE_DIR)/payload && chmod 777 $(COMPOSE_DIR)/payload
	@if [ ! -f "$(ENV_FILE)" ]; then \
		cp "$(ENV_EXAMPLE)" "$(ENV_FILE)"; \
		echo "Created $(ENV_FILE) from example."; \
	else \
		echo "$(ENV_FILE) already exists."; \
	fi

CONFIG ?= config/showcase.example.yaml

config-check: ## Ask both planes whether they would start with CONFIG=config/<file>.yaml
	@# **Before anything is deployed, not during a maintenance window.** An environment that is
	@# not `local` turns on a hardening check per plane and the first time most of them are met is
	@# when a container exits. This renders the file and hands it to the product's own
	@# `unsafe_settings`, in a subprocess with only that environment in it.
	@#
	@# Exit codes: 1 the file has problems of its own · 2 it does not render · 3 it declares a
	@# Vault this machine cannot use, which is not the same as an answer.
	uv run python tools/config_check.py $(CONFIG)

config-verify: ## Check deploy/compose/.env against the config file it came from
	@# **Not an errand.** Rendering puts a config file's values into `.env`, and Compose fills
	@# every gap from `${VAR:-default}` — so a value left empty, a variable the file does not name,
	@# a `.env` edited afterwards, or a source edited without re-rendering all end the same way:
	@# the deployment runs on something nobody chose, and nothing says so. This says so.
	@#
	@# `-` where it is called below, because a stack somebody started by hand has no config file
	@# and must still start; the message is the point, not the exit code.
	uv run python tools/config_render.py --verify $(ENV_FILE)

up: env ## Start the full stack (infra + observability)
	@-$(MAKE) --no-print-directory config-verify
	$(COMPOSE) up -d
	@# **The one address this target starts and nothing named.** `GRAFANA_URL` was defined beside
	@# the other seven and read by nothing, so the backend this target brings up had no way in
	@# except knowing the port — which is `LESSONS.md`'s *a named bound that nothing reads*, in the
	@# file that defines it. Printed with the condition attached, because the collector and Grafana
	@# are healthy whether or not anything sends: an empty Explore view looks identical to a broken
	@# one, and the difference is a flag that is off by default.
	@echo ""
	@echo "  Traces, metrics and logs:  $(GRAFANA_URL)   (Explore -> Tempo)"
	@echo "  The applications export only with AIRA_OTEL_ENABLED=true in deploy/compose/.env;"
	@echo "  without it the backend runs and receives nothing."

up-core: env ## Start only core infra (no observability backend)
	$(COMPOSE_CORE) up -d

up-apps: env ## Start the product: infra + the application processes, no demo provisioning
	@-$(MAKE) --no-print-directory config-verify
	$(COMPOSE_APPS) up -d --build

otel-arrivals: ## Watch what arrives at the collector, in its own words
	@# **What arrived**, which is a different question from what was delivered onward
	@# (`make otel-status`) and from what a service says it sent (`AIRA_DEBUG_INTEGRATIONS=otel`).
	@# Three levels, set in deploy/compose/.env and applied by recreating the collector:
	@#
	@#   AIRA_OTEL_DEBUG_VERBOSITY=basic     counts only
	@#   AIRA_OTEL_DEBUG_VERBOSITY=normal    one line per span   (the default)
	@#   AIRA_OTEL_DEBUG_VERBOSITY=detailed  every attribute, event and link
	@#
	@# For the same thing as OTLP/JSON — to parse rather than read — set
	@# `AIRA_OTEL_ARRIVED_FILE=/payload/arrived.json` and read deploy/compose/payload/arrived.json.
	docker logs -f --tail 100 $${AIRA_STACK:-aira}-otel-collector

otel-status: ## Did telemetry reach the collector, and did the collector pass it on?
	@# **The hop `AIRA_DEBUG_INTEGRATIONS=otel` cannot see.** That channel says the export left and
	@# what the next hop answered — and an OTLP 200 can carry a body saying half the batch was
	@# dropped, which the Python exporter reads as `SUCCESS`. This reads the collector's own
	@# `receiver_accepted` / `receiver_refused` against `exporter_sent` / `send_failed`, which is
	@# the difference between "we sent it" and "it arrived".
	@uv run python tools/otel_status.py

otlp-inspector: env ## Stand in for a SIEM: show what the forwarding leg actually sends
	@# **The leg `make otel-arrivals` cannot see.** That one is what *arrived* at the collector,
	@# before the SIEM filter and before anything is forwarded. This is what leaves on the second
	@# destination's own pipeline — the eleven request spans rather than the three hundred, with
	@# whatever credential the auth fragment put on the request.
	@#
	@# A debugging tool: in memory, capped, unauthenticated, and holding attribution — start it
	@# while you are wiring a destination up, and `make otlp-inspector-down` when you are done.
	$(COMPOSE_CORE) --profile debug up -d otlp-inspector
	@echo
	@# **Two addresses, and confusing them is a reported failure.**
	@#
	@# Inside this stack's network the receiver is `otlp-inspector:4318` — a Docker name and the
	@# *container's* port, both of which exist only on this machine. From anywhere else it is this
	@# host on the **published** port, and the same port serves the page and OTLP: one address is
	@# both the browser URL and the endpoint. Printing only the first sent somebody to configure a
	@# collector on another machine with a name that machine cannot resolve.
	@echo "  the page, and the OTLP endpoint, are the same address:"
	@echo "      $(INSPECTOR_URL)          from this machine"
	@echo "      http://<this-host>:$(INSPECTOR_PORT)     from anywhere else (needs AIRA_BIND_HOST=0.0.0.0)"
	@echo
	@# **Whether anything is actually pointed here**, rather than only where to look. Starting the
	@# receiver and configuring the collector are two steps, and the second is the one that gets
	@# forgotten. An empty page looks the same whether it was done, done wrongly, or not done — and
	@# the collector's own arguments are the one answer that cannot be stale.
	@if docker inspect $${AIRA_STACK:-aira}-otel-collector \
	     --format '{{range .Args}}{{println .}}{{end}}' 2>/dev/null | grep -q 'forward.yaml'; then \
	  echo "  the collector IS forwarding   (--config=…/forward.yaml is merged)"; \
	else \
	  echo "  the collector is NOT forwarding yet — nothing will arrive until it is:"; \
	  echo "      AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml"; \
	  echo "      AIRA_OTEL_FORWARD_ENDPOINT=http://otlp-inspector:4318   (collector on THIS machine)"; \
	  echo "      AIRA_OTEL_FORWARD_ENDPOINT=http://<host>:$(INSPECTOR_PORT)         (collector elsewhere)"; \
	  echo "    in deploy/compose/.env, then recreate the collector. The endpoint alone does"; \
	  echo "    nothing — the fragment is the switch."; \
	fi
	@echo

otlp-inspector-down: ## Stop the standing-in SIEM and forget what it held
	$(COMPOSE_CORE) --profile debug rm -sf otlp-inspector

up-full: env ## Start EVERYTHING in containers, demo provisioning included (infra + apps + showcase)
	@-$(MAKE) --no-print-directory config-verify
	$(COMPOSE_FULL) up -d --build
	@echo "SPA: $(CONSOLE_URL)   (login ucadmin / demo-password)"

showcase: env ## Start the full demo: stack, local model, seeded roles/budgets, and real traffic
	@echo "==> starting the stack (this pulls a model on the first run and takes a few minutes)"
	$(COMPOSE_FULL) --profile demo up -d --build
	@# `wait-healthy`, not a second loop of its own. This waited on the **gateway** alone and then
	@# printed "SPA $(CONSOLE_URL)" — so on a machine where the frontend took a few seconds
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
		s=$$(docker inspect -f '{{.State.Status}}' $${AIRA_STACK:-aira}-ollama-pull 2>/dev/null || echo gone); \
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
	@#
	@# `--assert-controls` is the second half of that, and it took a second run to find: a demo
	@# can also fail by refusing the two requests it exists to *demonstrate*. Measured
	@# 2026-08-26 — the injection attempt and the embedding batch both came back `429
	@# budget_exceeded`, refused by an allowance before the pipeline ran, and this target
	@# reported success. Only here, never in `showcase-traffic`: that one deliberately skips the
	@# reset, so reaching a limit is its point rather than its defect.
	uv run python tools/demo_traffic.py --assert-controls
	@echo ""
	@echo "  Start here:"
	@echo "    Console     $(CONSOLE_URL)   the SPA — everything below is reached from it"
	@echo "    Keycloak    $(KEYCLOAK_URL)/admin/master/console/#/aira/users   admin / admin"
	@# Read from the running realm, not asserted. Somebody opened the admin console, saw one user
	@# called `admin` and no groups, and concluded the seed had failed — it had not: that console
	@# signs you in to the *master* realm and the demo lives in `aira`. Two accounts named `admin`
	@# in two realms, and this block used to name neither.
	@-KEYCLOAK_URL=$(KEYCLOAK_URL) \
		KEYCLOAK_REALM_FILE=deploy/compose/keycloak/realms/aira-realm.json \
		python3 tools/keycloak_demo_realm.py --report
	@echo ""
	@echo "  Serving, with no user interface of their own:"
	@echo "    Gateway     $(GATEWAY_URL)   the API that models are called through"
	@echo "    Management  $(MANAGEMENT_URL)   the control-plane API (/api/v1/...)"
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
	@# What somebody watching this demo asks next: "how do I put *my* client behind it?" The
	@# answer is four administration steps and a base URL, and it is the same four steps for both
	@# surfaces — so the demo names them here rather than leaving a reader to infer them from a
	@# seed script. Both documents were executed end to end against a running stack before being
	@# written; neither is a description of what ought to work.
	@echo "==> putting your own client behind the gateway"
	@echo ""
	@echo "  Four steps, once per client, and then only the base URL changes:"
	@echo "    1. create a use case          POST /api/v1/use-cases/"
	@echo "    2. release the models it may call   PATCH  …/use-cases/<slug>/  {\"allowed_models\": […]}"
	@echo "       (empty means none — a new use case can call nothing until somebody releases one)"
	@echo "    3. add its people or a Keycloak group   POST …/members/  ·  POST …/groups/"
	@echo "    4. issue an API key           POST …/api-keys/   (shown once, bound to that use case)"
	@echo ""
	@echo "  A key belongs to one use case, so a client normally sends nothing else. A caller in"
	@echo "  several names one with the 'X-AIRA-Use-Case: <slug>' header — which chooses among what"
	@echo "  they already have and never grants anything."
	@echo ""
	@echo "    KIRA client   $(GATEWAY_URL)/kira/api/external   docs/MIGRATION-KIRA.md"
	@echo "    Gemini client $(GATEWAY_URL)/v1beta              docs/MIGRATION-GEMINI.md"
	@echo ""
	@# And one that runs *now*. The four steps above are what a reader needs to migrate; they are
	@# not what a reader needs to **try**, because the demo has already done all four for its own
	@# use cases. Values are read from the running catalog, so the id in the command is the id
	@# this installation assigned rather than one that was true when the block was written.
	@-uv run python tools/showcase_try_it.py
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

down-full: down ## Stop the full containerised stack (keeps volumes) — an alias for `down`
	@:
# Two names, one implementation, deliberately. `down-full` had the same hole one profile smaller
# (`COMPOSE_FULL` omits `verify`, so a `make verify-up` model survived it), and a second stopping
# target is a second place for the set to be wrong. `down` now covers everything either could
# reach, so the name is kept for whoever's fingers know it and the body is not written twice.

logs-apps: ## Tail logs of the application containers only
	$(COMPOSE_FULL) logs -f --tail=100 gateway gateway-consumer management management-relay frontend

build-images: ## Build the three application images without starting anything
	$(COMPOSE_FULL) build gateway management frontend

verify-up: env ## Start a real local model (FRD-123) and pull the two verification models
	$(COMPOSE) --profile verify up -d ollama
	@echo "waiting for the endpoint..."
	@until curl -fsS $(OLLAMA_URL)/api/version >/dev/null 2>&1; do sleep 1; done
	@echo "pulling models (hundreds of MB, once per machine)..."
	docker exec $${AIRA_STACK:-aira}-ollama ollama pull $(LOCAL_CHAT_MODEL)
	docker exec $${AIRA_STACK:-aira}-ollama ollama pull $(LOCAL_EMBED_MODEL)
	@echo
	@echo "Point the gateway at it:"
	@echo "  AIRA_OLLAMA_URL=$(OLLAMA_URL) \\"
	@echo "  AIRA_OLLAMA_MODELS=$(LOCAL_CHAT_MODEL) \\"
	@echo "  AIRA_OLLAMA_EMBEDDING_MODELS=$(LOCAL_EMBED_MODEL) make run-gateway-oidc"

verify-down: ## Stop the local model (keeps the downloaded weights)
	$(COMPOSE) --profile verify stop ollama

test-verify: ## Integration tests that need a real local model (skips cleanly without one)
	uv run pytest -m integration --no-cov tests/integration/test_local_model.py

down: ## Stop everything this repository can start (keeps volumes)
	$(COMPOSE_ALL) down --remove-orphans

destroy: ## Stop everything and remove volumes (fresh state)
	$(COMPOSE_ALL) down -v --remove-orphans

ps: ## Show service status and health
	$(COMPOSE_ALL) ps

logs: ## Tail logs of all services
	$(COMPOSE_ALL) logs -f --tail=100

restart: ## Restart the stack
	$(COMPOSE_ALL) restart

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
		if curl -sf $(CONSOLE_URL)/ >/dev/null 2>&1 \
			&& curl -sf $(GATEWAY_URL)/healthz >/dev/null 2>&1 \
			&& curl -sf $(MANAGEMENT_URL)/healthz >/dev/null 2>&1 \
			&& curl -sf $(KEYCLOAK_URL)/realms/aira/.well-known/openid-configuration >/dev/null 2>&1; \
		then echo "ready after $$((i * 3))s"; exit 0; fi; \
		sleep 3; \
	done; \
	echo "the stack did not become ready in time"; \
	$(COMPOSE_FULL) ps; exit 1

test-e2e: ## Run browser end-to-end tests (needs the stack + all services; see e2e/README.md)
	@# **Tidied whether or not it passed**, and the ordering here is the whole point. Written as
	@# two recipe lines first, which make abandons at the first failure — so the one run that
	@# leaves the most behind, the failing one, was exactly the run that never cleaned up. Measured:
	@# a red suite left 68 tombstones and a cleared register, which is nothing anybody could name
	@# again.
	@#
	@# Playwright's own teardown *retires* what the suite made (the product's path, `FRD-607`);
	@# this purges the tombstones, which is a demo-only step and cannot be reached over HTTP.
	@set -e; \
	( cd e2e && npm install --silent && npx playwright test ); status=$$?; \
	$(MAKE) --no-print-directory purge-e2e-use-cases || true; \
	exit $$status

purge-e2e-use-cases: ## Purge the use cases the browser suite retired (demo installations only)
	@if [ -s e2e/.artifacts/use-cases.txt ]; then \
		$(COMPOSE_FULL) exec -T -e AIRA_DEMO_MODE=true management \
			python -m django purge_test_use_cases --settings=aira_management.config.settings \
			$$(tr '\n' ' ' < e2e/.artifacts/use-cases.txt) \
		&& rm -f e2e/.artifacts/use-cases.txt; \
	else \
		echo "[purge] no register at e2e/.artifacts/use-cases.txt; nothing to purge"; \
	fi

e2e: test-e2e

lint: lint-py lint-frontend ## Run all linters/type-checks (check mode)

lint-py: ## Run ruff lint + format check + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy gateway/src libs/src management/backend/src

lint-frontend: ## Check frontend formatting (Prettier) and types (console build + e2e tsc)
	cd $(FRONTEND_DIR) && npx prettier --check "src/**/*.{ts,html,scss}"
	cd $(FRONTEND_DIR) && npx ng build --configuration development
	cd $(FRONTEND_DIR) && npx prettier --check "proxy.conf.cjs"
	@# **The browser suite is TypeScript too, and nothing type-checked it.** `ng build` covers the
	@# console and stops at its own `src/`; `e2e/` has `"strict": true` in its tsconfig and no
	@# reader. A spread over `NodeListOf<Element>` sat in `layout.spec.ts` compiling fine under
	@# Playwright's own transpile and failing under `tsc` — found on 2026-08-18 by running the
	@# check that had never been wired up. A rule only a reviewer enforces is one the next file
	@# breaks; this is the same shape as the ESLint claim `CLAUDE.md` had to retract.
	cd e2e && npx prettier --check "**/*.ts" && npx tsc --noEmit -p tsconfig.json

fmt: ## Auto-format and auto-fix the whole codebase
	uv run ruff format .
	uv run ruff check --fix .
	cd $(FRONTEND_DIR) && npx prettier --write "src/**/*.{ts,html,scss}" "proxy.conf.cjs"
	cd e2e && npx prettier --write "**/*.ts"

# Local run targets enable OTLP export to the collector (make up starts it).
run-gateway: ## Run the Gateway API locally against the Compose stack
	AIRA_OTEL_ENABLED=true uv run uvicorn aira_gateway.main:app --reload --port 8001

# The SPA's dry-run and consumption views send their Keycloak bearer to the gateway, so it has
# to be able to verify it (ADR-0007).
run-gateway-oidc: ## Run the Gateway with OIDC enabled (required for the SPA's gateway views)
	AIRA_OTEL_ENABLED=true AIRA_OIDC_ENABLED=true \
		AIRA_OIDC_ISSUER=$(KEYCLOAK_URL)/realms/aira \
		uv run uvicorn aira_gateway.main:app --reload --port 8001

run-backend: ## Run the Management backend (Django) locally against the Compose stack
	cd management/backend && AIRA_OTEL_ENABLED=true uv run python manage.py runserver 127.0.0.1:$(MANAGEMENT_PORT)

run-frontend: ## Run the Angular dev server (proxies /api to the management backend)
	cd $(FRONTEND_DIR) && npx ng serve --host 0.0.0.0 --port $(CONSOLE_PORT) --proxy-config proxy.conf.cjs

migrate-gateway: ## Apply gateway DB migrations (Alembic)
	cd gateway && uv run alembic upgrade head

kafka-topics: ## Create the compacted config-distribution topics (idempotent)
	@for t in aira.usecases aira.memberships aira.api-keys aira.pipelines aira.budgets aira.rate-limits aira.models aira.anomaly-rules; do \
		docker exec $${AIRA_STACK:-aira}-kafka /opt/kafka/bin/kafka-topics.sh --create --if-not-exists --topic $$t \
			--bootstrap-server $(KAFKA_ADDR) --partitions 1 --replication-factor 1 \
			--config cleanup.policy=compact; \
	done

prune: ## Apply the retention periods (removes stored payloads past their period)
	uv run python -m aira_gateway.retention

relay: ## Publish pending management outbox events to Kafka
	cd management/backend && uv run python manage.py relay

consume: ## Run the gateway config consumer (applies events into the read-model)
	uv run python -m aira_gateway.consumer.worker

vault-init: ## Create the Vault path, policy and AppRole; optionally copy AIRA_* secrets in
	@echo "Setting up Vault at $${VAULT_ADDR:-$(VAULT_URL)}…"
	uv run python tools/vault_setup.py --from-env

vault-status: ## Where does the gateway say its secrets came from?
	@curl -s $(GATEWAY_URL)/readyz | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('secrets', {'note': 'authenticate to see this'}), indent=2))"

seed: ## Migrate + seed demo data (idempotent; requires 'make up')
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py migrate --noinput
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo

seed-reset: ## Reset and reseed demo data
	cd management/backend && AIRA_DEMO_MODE=true uv run python manage.py seed_demo --fresh
