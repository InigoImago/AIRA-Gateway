# Testing strategy

AIRA separates tests into two tiers so the fast tier runs anywhere (including CI) **without any
infrastructure**, and the slow tier verifies real integrations on demand.

## Tier 1 — Unit/component tests (hermetic, default)
Run everywhere, no services required. This is what a CI unit stage runs on every commit.

- **Python** (`make test-py` → `uv run pytest`): uses **in-memory SQLite** (auto-detected under
  pytest), a **fake JWKS resolver** + self-signed RS256 for OIDC, the **deterministic mock provider**,
  and FastAPI `TestClient` / `httpx.ASGITransport`. No Postgres/Keycloak/Kafka/OTel needed.
- **Frontend** (`make test-frontend`): Angular unit tests via **Vitest + jsdom** — no browser.
- Enforced: `ruff`, `ruff format`, `mypy --strict`, `prettier`, and a coverage **floor** — not the
  100% this line used to claim. The floors are where they are so that CI fails on a *drop*, and
  they are deliberately below the actual figures:

  | | gate | measured 2026-08-20 |
  | --- | --- | --- |
  | Python (`--cov-fail-under`) | **90%** | 95.68% |
  | Frontend statements | 90% | 93.13% |
  | Frontend branches | **92%** | 92.08% |
  | Frontend lines | 93% | 94.78% |
  | Frontend functions | 75% | 76.31% |

  Branches is the one with almost no headroom, which is why adding a control to the console without
  testing it fails the build rather than drifting quietly. **A number here is a floor and not an
  achievement**, and this document said the opposite for as long as it existed: 100% enforced would
  mean no line could be added without a test that reaches it, and that is not what CI does.

Proof: with the **entire Compose stack stopped**, `uv run pytest` passes all tests.

**With the stack _running_, the hermetic tier is not fully hermetic**, and it is worth knowing
which way. The database is in memory per app, but the **budget counter** is not: `ADR-0008` keeps
it in a shared store, so where a Redis is reachable — a developer's machine, a sandbox — a key
written by one run is still there for the next. A test that fixes a use-case slug therefore depends
on how many times it has been run. Measured, on a test written this way: the first assertion
refused a request that should have been served. **Give a test its own slug**; the suite does.

## Tier 1b — Mutation checks (hermetic, on demand)
`make mutants` (`tools/mutation_check.py`) breaks one property at a time and requires a named test
to notice. **604 properties** as of 2026-08-26; the figure is stated in `CLAUDE.md` and a test
fails when the two disagree, because a claim about how much of a system is checked is exactly the
sort that rots quietly. It is the answer to *"a green test proves only that the code and the test agree"*: a
property nothing would notice losing is reported as a **survivor**, and a mutation whose anchor has
moved as **stale** — never as a pass. Run it when adding a rule worth keeping, and add the mutation
that reintroduces a bug you have just fixed.

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

## Tier 3 — Browser tests (`e2e/`, need the whole thing)
Playwright against the running console, gateway and Keycloak (`make test-e2e`). This layer exists
for what the ones above **structurally cannot see**: a real authorization-code flow, a control that
renders and does nothing, a layout that only a viewport has an opinion about. Anything needing a
*user token* belongs here — the dev realm has the password grant off, so a token only comes from
the real flow.

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
