# AIRA Gateway — AI REST API

**AIRA Gateway** is an enterprise-grade AI gateway: a single, unified, provider-agnostic REST API
in front of multiple upstream LLM platforms, with centralized authentication, authorization,
routing, governance, observability, and security.

It consists of two self-developed components plus open-source infrastructure:

1. **Gateway API** (FastAPI) — the data plane: unified API (**Gemini-compatible first**, OpenAI
   later), auth, attribution, routing/fallback, pipeline enforcement, request/response persistence,
   tracing.
2. **Management & Monitoring** (Angular + Django REST Framework) — the control plane: use-case
   self-service, pipeline builder, budgets, anomaly rules, IT Security console, governance views.

Infrastructure: **PostgreSQL**, **Keycloak** (SSO), **Apache Kafka** (event bus). Local
infrastructure runs via **Docker Compose**; Kubernetes/Helm is planned.

## Quickstart

Everything in containers — infrastructure plus all five application processes:

```bash
make up-full
```

Then open <http://localhost:4200> and log in as `ucadmin` / `demo-password`.
Only Docker is required for this; `make down-full` stops it again.

### Running from source instead

For development with reload-on-save you also need Python **3.14** with
[`uv`](https://docs.astral.sh/uv/) and Node **26**:

```bash
make sync              # dependencies (Python + frontend)
make up                # infrastructure only
make seed              # management DB: migrate + demo roles/users
make migrate-gateway   # gateway DB: alembic migrations
make kafka-topics      # the five compacted config topics
```

Then, each in its own terminal:

```bash
make run-gateway-oidc  # gateway            :8001
make run-backend       # management API     :8002
make consume           # config consumer    (long-running)
make run-frontend      # SPA                :4200
```

In this mode the outbox relay is not running, so after changing members, API keys, pipelines or
budgets in the UI, run `make relay` — that is how the gateway learns about them. (`make up-full`
runs it in a loop for you.)

**→ Full deployment guide, configuration reference and integration notes:
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)**

## Development

```bash
make ci                # everything CI checks: lint + types + unit tests with coverage gates
make test-integration  # server-side checks against the running stack
make test-e2e          # browser end-to-end (Playwright) — see e2e/README.md
make fmt               # auto-format everything
make help              # all targets
```

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the same targets on every push: the
two hermetic jobs, plus a third that builds the images, brings the whole stack up and runs the
integration and browser suites against it. Every step calls a `make` target, so a green local run
means a green CI run.

Three test layers, each covering what the one below cannot: unit (hermetic) →
[`tests/integration/`](tests/integration/) (live stack) → [`e2e/`](e2e/) (real browser).

## Documentation

- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — running it standalone, integrating it, every config value
- [`docs/PRD.md`](docs/PRD.md) — Project Requirements Document
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phased delivery plan
- [`docs/DEVLOG.md`](docs/DEVLOG.md) — dated log of what changed and why
- [`docs/adr/`](docs/adr/) — Architecture Decision Records
- [`docs/features/`](docs/features/) — Feature Requirement Documents (FRDs)
- [`CLAUDE.md`](CLAUDE.md) — conventions and current status

## Status

**Phases 0–4 delivered**: infrastructure and observability, the Gemini-compatible gateway with
auth/attribution/persistence, the management control plane with RBAC and Kafka config
distribution, the Angular SPA, the pre-dispatch pipeline (injection filter, model allow-list,
LLM routing), and budgets with enforcement.

Next: CI, per-caller rate limiting, then Phase 5 (anomaly detection and the IT Security console).
Known gaps are listed in [`docs/DEPLOYMENT.md §7`](docs/DEPLOYMENT.md#7-known-gaps).

Documentation and code are written in **English**.
