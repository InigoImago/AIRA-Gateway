# Testing strategy

AIRA separates tests into two tiers so the fast tier runs anywhere (including CI) **without any
infrastructure**, and the slow tier verifies real integrations on demand.

## Tier 1 — Unit/component tests (hermetic, default)
Run everywhere, no services required. This is what a CI unit stage runs on every commit.

- **Python** (`make test-py` → `uv run pytest`): uses **in-memory SQLite** (auto-detected under
  pytest), a **fake JWKS resolver** + self-signed RS256 for OIDC, the **deterministic mock provider**,
  and FastAPI `TestClient` / `httpx.ASGITransport`. No Postgres/Keycloak/Kafka/OTel needed.
- **Frontend** (`make test-frontend`): Angular unit tests via **Vitest + jsdom** — no browser.
- Enforced: **100% coverage gate**, `ruff`, `ruff format`, `mypy --strict`, `prettier`.

Proof: with the **entire Compose stack stopped**, `uv run pytest` passes all tests.

## Tier 2 — Integration tests (need the live stack, opt-in)
Marked `@pytest.mark.integration` and **excluded from the default run** (`-m 'not integration'` in
`addopts`). They exercise the real Postgres/Keycloak/Kafka. Run them in a dedicated CI stage that
first brings the stack up:

```bash
make up                 # start Postgres, Keycloak, Kafka, Vault, OTel+Grafana
make migrate-gateway    # apply gateway migrations
make seed               # (optional) demo data
make test-integration   # uv run pytest -m integration --no-cov
```

Codify the manual end-to-end checks (auth flows, use-case membership, persistence, SSE) as
`integration`-marked tests under `*/tests/integration/` so they are repeatable in CI.

## Jenkins pipeline (sketch)
```groovy
pipeline {
  agent any
  stages {
    stage('Lint & Unit') {            // fast, hermetic — no services
      steps {
        sh 'uv sync'
        sh 'make lint'
        sh 'make test-py'             // in-memory SQLite; no Postgres/Keycloak needed
        sh 'make test-frontend'
      }
    }
    stage('Integration') {            // only where Docker is available
      when { anyOf { branch 'main'; changeRequest() } }
      steps {
        sh 'make up'
        sh 'make migrate-gateway'
        sh 'make test-integration'
      }
      post { always { sh 'make down' } }
    }
  }
}
```

Notes:
- The unit stage needs only Python (`uv`) + Node — **no Docker**. Ideal for a locked-down agent.
- The integration stage needs a Docker-capable agent. For fully isolated integration tests you can
  later swap the shared Compose stack for **testcontainers** (spin up ephemeral Postgres/Keycloak
  per test session) so parallel builds don't share state.
